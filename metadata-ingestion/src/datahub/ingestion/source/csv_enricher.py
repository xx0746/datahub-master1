import csv
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Set, Tuple

from datahub.emitter.mcp import MetadataChangeProposalWrapper
from datahub.ingestion.api.common import PipelineContext
from datahub.ingestion.api.decorators import (
    SupportStatus,
    config_class,
    platform_name,
    support_status,
)
from datahub.ingestion.api.source import Source, SourceReport
from datahub.ingestion.api.workunit import MetadataWorkUnit
from datahub.ingestion.source_config.csv_enricher import CSVEnricherConfig
from datahub.metadata.schema_classes import (
    AuditStampClass,
    ChangeTypeClass,
    EditableSchemaFieldInfoClass,
    EditableSchemaMetadataClass,
    GlobalTagsClass,
    GlossaryTermAssociationClass,
    GlossaryTermsClass,
    OwnerClass,
    OwnershipClass,
    OwnershipTypeClass,
    TagAssociationClass,
)
from datahub.utilities.urns.dataset_urn import DatasetUrn
from datahub.utilities.urns.urn import Urn

SCHEMA_ASPECT_NAME = "editableSchemaMetadata"
DATASET_ENTITY_TYPE = "dataset"
GLOSSARY_TERMS_ASPECT_NAME = "glossaryTerms"
TAGS_ASPECT_NAME = "globalTags"
OWNERSHIP_ASPECT_NAME = "ownership"
ACTOR = "urn:li:corpuser:ingestion"


def get_audit_stamp() -> AuditStampClass:
    now = int(time.time() * 1000)
    return AuditStampClass(now, ACTOR)


def maybe_remove_prefix(s: str, prefix: str) -> str:
    if not s.startswith(prefix):
        return s
    return s[len(prefix) :]


def maybe_remove_suffix(s: str, suffix: str) -> str:
    if not s.endswith(suffix):
        return s
    return s[: -len(suffix)]


def sanitize_array_string(s: str) -> str:
    return maybe_remove_suffix(maybe_remove_prefix(s, "["), "]")


@dataclass
class SubResourceRow:
    entity_urn: str
    field_path: str
    term_associations: List[GlossaryTermAssociationClass]
    tag_associations: List[TagAssociationClass]


@dataclass
class CSVEnricherReport(SourceReport):
    num_glossary_term_workunits_produced: int = 0
    num_tag_workunits_produced: int = 0
    num_owners_workunits_produced: int = 0
    num_editable_schema_metadata_workunits_produced: int = 0


@platform_name("CSV")
@config_class(CSVEnricherConfig)
@support_status(SupportStatus.INCUBATING)
class CSVEnricherSource(Source):
    """
    This plugin is used to apply glossary terms, tags and owners at the entity level. It can also be used to apply tags
    and glossary terms at the column level. These values are read from a CSV file and can be used to either overwrite
    or append the above aspects to entities.

    The format of the CSV must be like so, with a few example rows.

    | resource                                                                 | subresource | glossary_terms                        | tags                | owners                                          |
    |--------------------------------------------------------------------------|-------------|---------------------------------------|---------------------|-------------------------------------------------|
    | urn:li:dataset:(urn:li:dataPlatform:hive,SampleHiveDataset,PROD)         |             | [urn:li:glossaryTerm:AccountBalance]  | [urn:li:tag:Legacy] | [urn:li:corpuser:datahub]                       |
    | urn:li:dataset:(urn:li:dataPlatform:bigquery,SampleBigqueryDataset,PROD) | field_foo   | [urn:li:glossaryTerm:CustomerAccount] |                     |                                                 |
    | urn:li:dataset:(urn:li:dataPlatform:redshift,SampleRedshiftDataset,PROD) | field_bar   |                                       | [urn:li:tag:Legacy] |                                                 |

    Note that the first row does not have a subresource populated. That means any glossary terms, tags, and owners will
    be applied at the entity field. If a subresource IS populated (as it is for the second and third rows), glossary
    terms and tags will be applied on the subresource. Every row MUST have a resource. Also note that owners can only
    be applied at the resource level and will be ignored if populated for a row with a subresource.
    """

    # @classmethod
    # def create(cls, config_dict: dict, ctx: PipelineContext) -> Source:
    #     config = CSVEnricherConfig.parse_obj(config_dict)
    #     return cls(config, ctx)

    def __init__(self, config: CSVEnricherConfig, ctx: PipelineContext):
        super().__init__(ctx)
        self.config: CSVEnricherConfig = config
        self.ctx: PipelineContext = ctx
        self.report: CSVEnricherReport = CSVEnricherReport()
        # Map from entity urn to a list of SubResourceRow.
        self.editable_schema_metadata_map: Dict[str, List[SubResourceRow]] = {}

    def get_resource_glossary_terms_work_unit(
        self,
        entity_urn: str,
        entity_type: str,
        term_associations: List[GlossaryTermAssociationClass],
    ) -> Optional[MetadataWorkUnit]:
        # Check if there are glossary terms to add. If not, return None.
        if len(term_associations) <= 0:
            return None

        current_terms: Optional[GlossaryTermsClass] = None
        if not self.config.should_overwrite:
            # Cannot append if the DataHub graph is None
            if not self.ctx.graph:
                return None

            # Get the existing terms for the entity from the DataHub graph
            current_terms = self.ctx.graph.get_glossary_terms(entity_urn=entity_urn)

        if not current_terms:
            # If we want to overwrite or there are no existing terms, create a new GlossaryTerms object
            current_terms = GlossaryTermsClass(term_associations, get_audit_stamp())
        else:
            current_term_urns: Set[str] = set(
                [term.urn for term in current_terms.terms]
            )
            term_associations_filtered: List[GlossaryTermAssociationClass] = [
                association
                for association in term_associations
                if association.urn not in current_term_urns
            ]
            # If there are no new glossary terms to add, we don't need to emit a work unit.
            if len(term_associations_filtered) <= 0:
                return None

            # Add any terms that don't already exist in the existing GlossaryTerms object to the object
            current_terms.terms.extend(term_associations_filtered)

        terms_mcpw: MetadataChangeProposalWrapper = MetadataChangeProposalWrapper(
            entityType=entity_type,
            entityUrn=entity_urn,
            changeType=ChangeTypeClass.UPSERT,
            aspectName=GLOSSARY_TERMS_ASPECT_NAME,
            aspect=current_terms,
        )
        terms_wu: MetadataWorkUnit = MetadataWorkUnit(
            id=f"{entity_urn}-{GLOSSARY_TERMS_ASPECT_NAME}",
            mcp=terms_mcpw,
        )
        return terms_wu

    def get_resource_tags_work_unit(
        self,
        entity_urn: str,
        entity_type: str,
        tag_associations: List[TagAssociationClass],
    ) -> Optional[MetadataWorkUnit]:
        # Check if there are tags to add. If not, return None.
        if len(tag_associations) <= 0:
            return None

        current_tags: Optional[GlobalTagsClass] = None
        if not self.config.should_overwrite:
            # Cannot append if the DataHub graph is None
            if not self.ctx.graph:
                return None

            # Get the existing tags for the entity from the DataHub graph
            current_tags = self.ctx.graph.get_tags(entity_urn=entity_urn)

        if not current_tags:
            # If we want to overwrite or there are no existing tags, create a new GlobalTags object
            current_tags = GlobalTagsClass(tag_associations)
        else:
            current_tag_urns: Set[str] = set([tag.tag for tag in current_tags.tags])
            tag_associations_filtered: List[TagAssociationClass] = [
                association
                for association in tag_associations
                if association.tag not in current_tag_urns
            ]
            # If there are no new tags to add, we don't need to emit a work unit.
            if len(tag_associations_filtered) <= 0:
                return None

            # Add any terms that don't already exist in the existing GlobalTags object to the object
            current_tags.tags.extend(tag_associations_filtered)

        tags_mcpw: MetadataChangeProposalWrapper = MetadataChangeProposalWrapper(
            entityType=entity_type,
            entityUrn=entity_urn,
            changeType=ChangeTypeClass.UPSERT,
            aspectName=TAGS_ASPECT_NAME,
            aspect=current_tags,
        )
        tags_wu: MetadataWorkUnit = MetadataWorkUnit(
            id=f"{entity_urn}-{TAGS_ASPECT_NAME}",
            mcp=tags_mcpw,
        )
        return tags_wu

    def get_resource_owners_work_unit(
        self,
        entity_urn: str,
        entity_type: str,
        owners: List[OwnerClass],
    ) -> Optional[MetadataWorkUnit]:
        # Check if there are owners to add. If not, return None.
        if len(owners) <= 0:
            return None

        current_ownership: Optional[OwnershipClass] = None
        if not self.config.should_overwrite:
            # Cannot append if the DataHub graph is None
            if not self.ctx.graph:
                return None

            # Get the existing owner for the entity from the DataHub graph
            current_ownership = self.ctx.graph.get_ownership(entity_urn=entity_urn)

        if not current_ownership:
            # If we want to overwrite or there are no existing tags, create a new GlobalTags object
            current_ownership = OwnershipClass(owners, get_audit_stamp())
        else:
            current_owner_urns: Set[str] = set(
                [owner.owner for owner in current_ownership.owners]
            )
            owners_filtered: List[OwnerClass] = [
                owner for owner in owners if owner.owner not in current_owner_urns
            ]
            # If there are no new owners to add, we don't need to emit a work unit.
            if len(owners_filtered) <= 0:
                return None

            # Add any terms that don't already exist in the existing GlobalTags object to the object
            current_ownership.owners.extend(owners_filtered)

        owners_mcpw: MetadataChangeProposalWrapper = MetadataChangeProposalWrapper(
            entityType=entity_type,
            entityUrn=entity_urn,
            changeType=ChangeTypeClass.UPSERT,
            aspectName=OWNERSHIP_ASPECT_NAME,
            aspect=current_ownership,
        )
        owners_wu: MetadataWorkUnit = MetadataWorkUnit(
            id=f"{entity_urn}-{OWNERSHIP_ASPECT_NAME}",
            mcp=owners_mcpw,
        )
        return owners_wu

    def process_sub_resource_row(
        self,
        sub_resource_row: SubResourceRow,
        current_editable_schema_metadata: EditableSchemaMetadataClass,
        needs_write: bool,
    ) -> Tuple[EditableSchemaMetadataClass, bool]:
        field_path: str = sub_resource_row.field_path
        term_associations: List[
            GlossaryTermAssociationClass
        ] = sub_resource_row.term_associations
        tag_associations: List[TagAssociationClass] = sub_resource_row.tag_associations
        has_terms: bool = len(term_associations) > 0
        has_tags: bool = len(tag_associations) > 0

        # We can skip this row if there are no tags or terms to add.
        if not has_tags and not has_terms:
            return current_editable_schema_metadata, needs_write

        # Objects that may or not be written depending on which conditions get triggered.
        field_info_to_set = EditableSchemaFieldInfoClass(fieldPath=field_path)
        terms_aspect = (
            GlossaryTermsClass(term_associations, get_audit_stamp())
            if has_terms
            else None
        )
        if terms_aspect:
            field_info_to_set.glossaryTerms = terms_aspect
        tags_aspect = GlobalTagsClass(tag_associations) if has_tags else None
        if tags_aspect:
            field_info_to_set.globalTags = tags_aspect

        # Boolean field to tell whether we have found a field match.
        field_match = False
        for field_info in current_editable_schema_metadata.editableSchemaFieldInfo:
            if (
                DatasetUrn._get_simple_field_path_from_v2_field_path(
                    field_info.fieldPath
                )
                == field_path
            ):
                # we have some editable schema metadata for this field
                field_match = True
                if has_terms:
                    if field_info.glossaryTerms and not self.config.should_overwrite:
                        current_term_urns = set(
                            [term.urn for term in field_info.glossaryTerms.terms]
                        )
                        term_associations_filtered = [
                            association
                            for association in term_associations
                            if association.urn not in current_term_urns
                        ]
                        if len(term_associations_filtered) > 0:
                            field_info.glossaryTerms.terms.extend(
                                term_associations_filtered
                            )
                            needs_write = True
                    else:
                        field_info.glossaryTerms = terms_aspect
                        needs_write = True

                if has_tags:
                    if field_info.globalTags and not self.config.should_overwrite:
                        current_tag_urns = set(
                            [tag.tag for tag in field_info.globalTags.tags]
                        )
                        tag_associations_filtered = [
                            association
                            for association in tag_associations
                            if association.tag not in current_tag_urns
                        ]
                        if len(tag_associations_filtered) > 0:
                            field_info.globalTags.tags.extend(tag_associations_filtered)
                            needs_write = True
                    else:
                        field_info.globalTags = tags_aspect
                        needs_write = True

        if not field_match:
            # this field isn't present in the editable schema metadata aspect, add it
            current_editable_schema_metadata.editableSchemaFieldInfo.append(
                field_info_to_set
            )
            needs_write = True
        return current_editable_schema_metadata, needs_write

    def get_sub_resource_work_units(self) -> Iterable[MetadataWorkUnit]:
        # Iterate over the map
        for entity_urn in self.editable_schema_metadata_map:
            # Boolean field to tell whether we need to write an MCPW.
            needs_write = False

            current_editable_schema_metadata: Optional[
                EditableSchemaMetadataClass
            ] = None
            if not self.config.should_overwrite:
                # Cannot append if the DataHub graph is None
                if not self.ctx.graph:
                    continue

                # Fetch the current editable schema metadata
                current_editable_schema_metadata = self.ctx.graph.get_aspect_v2(
                    entity_urn=entity_urn,
                    aspect=SCHEMA_ASPECT_NAME,
                    aspect_type=EditableSchemaMetadataClass,
                )

            # Create a new editable schema metadata for the dataset if it doesn't exist
            if not current_editable_schema_metadata:
                current_editable_schema_metadata = EditableSchemaMetadataClass(
                    editableSchemaFieldInfo=[],
                    created=get_audit_stamp(),
                )
                needs_write = True

            # Iterate over each sub resource row
            for sub_resource_row in self.editable_schema_metadata_map[
                entity_urn
            ]:  # type: SubResourceRow
                (
                    current_editable_schema_metadata,
                    needs_write,
                ) = self.process_sub_resource_row(
                    sub_resource_row, current_editable_schema_metadata, needs_write
                )

            # Write an MCPW if needed.
            if needs_write:
                editable_schema_metadata_mcpw: MetadataChangeProposalWrapper = (
                    MetadataChangeProposalWrapper(
                        entityType=DATASET_ENTITY_TYPE,
                        changeType=ChangeTypeClass.UPSERT,
                        entityUrn=entity_urn,
                        aspectName=SCHEMA_ASPECT_NAME,
                        aspect=current_editable_schema_metadata,
                    )
                )
                wu: MetadataWorkUnit = MetadataWorkUnit(
                    id=f"{entity_urn}-{SCHEMA_ASPECT_NAME}",
                    mcp=editable_schema_metadata_mcpw,
                )
                yield wu

    def maybe_extract_glossary_terms(
        self, row: Dict[str, str]
    ) -> List[GlossaryTermAssociationClass]:
        if not row["glossary_terms"]:
            return []

        # Sanitizing the terms string to just get the list of term urns
        terms_array_string = sanitize_array_string(row["glossary_terms"])
        term_urns: List[str] = terms_array_string.split(self.config.array_delimiter)
        term_associations: List[GlossaryTermAssociationClass] = [
            GlossaryTermAssociationClass(term) for term in term_urns
        ]
        return term_associations

    def maybe_extract_tags(self, row: Dict[str, str]) -> List[TagAssociationClass]:
        if not row["tags"]:
            return []

        # Sanitizing the tags string to just get the list of tag urns
        tags_array_string = sanitize_array_string(row["tags"])
        tag_urns: List[str] = tags_array_string.split(self.config.array_delimiter)
        tag_associations: List[TagAssociationClass] = [
            TagAssociationClass(tag) for tag in tag_urns
        ]
        return tag_associations

    def maybe_extract_owners(
        self, row: Dict[str, str], is_resource_row: bool
    ) -> List[OwnerClass]:
        if not is_resource_row:
            return []

        if not row["owners"]:
            return []

        # Sanitizing the owners string to just get the list of owner urns
        owners_array_string = sanitize_array_string(row["owners"])
        owner_urns: List[str] = owners_array_string.split(self.config.array_delimiter)
        owners: List[OwnerClass] = [
            OwnerClass(owner_urn, type=OwnershipTypeClass.NONE)
            for owner_urn in owner_urns
        ]
        return owners

    def get_workunits(self) -> Iterable[MetadataWorkUnit]:
        with open(self.config.filename, "r") as f:
            rows = csv.DictReader(f, delimiter=self.config.delimiter)
            for row in rows:
                # We need the resource to move forward
                if not row["resource"]:
                    continue

                is_resource_row: bool = not row["subresource"]

                entity_urn = row["resource"]
                entity_type = Urn.create_from_string(row["resource"]).get_type()

                term_associations: List[
                    GlossaryTermAssociationClass
                ] = self.maybe_extract_glossary_terms(row)

                # If this is a resource row, try to emit an MCP
                if len(term_associations) > 0 and is_resource_row:
                    maybe_terms_wu: Optional[
                        MetadataWorkUnit
                    ] = self.get_resource_glossary_terms_work_unit(
                        entity_urn=entity_urn,
                        entity_type=entity_type,
                        term_associations=term_associations,
                    )
                    if maybe_terms_wu:
                        self.report.num_glossary_term_workunits_produced += 1
                        self.report.report_workunit(maybe_terms_wu)
                        yield maybe_terms_wu

                tag_associations: List[TagAssociationClass] = self.maybe_extract_tags(
                    row
                )

                # If this a resource row, try to emit an MCP
                if len(tag_associations) > 0 and is_resource_row:
                    maybe_tags_wu: Optional[
                        MetadataWorkUnit
                    ] = self.get_resource_tags_work_unit(
                        entity_urn=entity_urn,
                        entity_type=entity_type,
                        tag_associations=tag_associations,
                    )
                    if maybe_tags_wu:
                        self.report.num_tag_workunits_produced += 1
                        self.report.report_workunit(maybe_tags_wu)
                        yield maybe_tags_wu

                owners: List[OwnerClass] = self.maybe_extract_owners(
                    row, is_resource_row
                )
                if len(owners) > 0:
                    maybe_owners_wu: Optional[
                        MetadataWorkUnit
                    ] = self.get_resource_owners_work_unit(
                        entity_urn=entity_urn,
                        entity_type=entity_type,
                        owners=owners,
                    )
                    if maybe_owners_wu:
                        self.report.num_owners_workunits_produced += 1
                        self.report.report_workunit(maybe_owners_wu)
                        yield maybe_owners_wu

                # Check if this row is applying aspects at the subresource level. Note that this only corresponds
                # to EditableSchemaMetadata for now.
                if not is_resource_row:
                    # Only dataset sub-resources are currently supported.
                    if entity_type != DATASET_ENTITY_TYPE:
                        continue

                    field_path = row["subresource"]
                    if entity_urn not in self.editable_schema_metadata_map:
                        self.editable_schema_metadata_map[entity_urn] = []
                    # Add the row to the map from entity (dataset) to SubResource rows. We cannot emit work units for
                    # EditableSchemaMetadata until we parse the whole CSV due to read-modify-write issues.
                    self.editable_schema_metadata_map[entity_urn].append(
                        SubResourceRow(
                            entity_urn=entity_urn,
                            field_path=field_path,
                            term_associations=term_associations,
                            tag_associations=tag_associations,
                        )
                    )

        # Yield sub resource work units once the map has been fully populated.
        for wu in self.get_sub_resource_work_units():
            self.report.workunits_produced += 1
            self.report.num_editable_schema_metadata_workunits_produced += 1
            self.report.report_workunit(wu)
            yield wu

    def get_report(self):
        return self.report

    def close(self):
        pass
