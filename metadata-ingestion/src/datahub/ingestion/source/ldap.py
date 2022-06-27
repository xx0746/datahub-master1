"""LDAP Source"""
import dataclasses
from typing import Any, Dict, Iterable, List, Optional

import ldap
from ldap.controls import SimplePagedResultsControl
from pydantic.fields import Field

from datahub.configuration.common import ConfigModel, ConfigurationError
from datahub.ingestion.api.common import PipelineContext
from datahub.ingestion.api.decorators import (
    SupportStatus,
    config_class,
    platform_name,
    support_status,
)
from datahub.ingestion.api.source import Source, SourceReport
from datahub.ingestion.api.workunit import MetadataWorkUnit
from datahub.metadata.com.linkedin.pegasus2avro.mxe import MetadataChangeEvent
from datahub.metadata.schema_classes import (
    CorpGroupInfoClass,
    CorpGroupSnapshotClass,
    CorpUserInfoClass,
    CorpUserSnapshotClass,
)

# default mapping for attrs
user_attrs_map: Dict[str, Any] = {}
group_attrs_map: Dict[str, Any] = {}

# general attrs
user_attrs_map["urn"] = "sAMAccountName"

# user related attrs
user_attrs_map["fullName"] = "cn"
user_attrs_map["lastName"] = "sn"
user_attrs_map["firstName"] = "givenName"
user_attrs_map["displayName"] = "displayName"
user_attrs_map["managerUrn"] = "manager"
user_attrs_map["email"] = "mail"
user_attrs_map["departmentId"] = "departmentNumber"
user_attrs_map["title"] = "title"
user_attrs_map["departmentName"] = "departmentNumber"
user_attrs_map["countryCode"] = "countryCode"

# group related attrs
group_attrs_map["urn"] = "cn"
group_attrs_map["email"] = "mail"
group_attrs_map["admins"] = "owner"
group_attrs_map["members"] = "uniqueMember"
group_attrs_map["displayName"] = "name"
group_attrs_map["description"] = "info"


def create_controls(pagesize: int) -> SimplePagedResultsControl:
    """
    Create an LDAP control with a page size of "pagesize".
    """
    return SimplePagedResultsControl(True, size=pagesize, cookie="")


def get_pctrls(
    serverctrls: List[SimplePagedResultsControl],
) -> List[SimplePagedResultsControl]:
    """
    Lookup an LDAP paged control object from the returned controls.
    """
    return [
        c for c in serverctrls if c.controlType == SimplePagedResultsControl.controlType
    ]


def set_cookie(
    lc_object: SimplePagedResultsControl,
    pctrls: List[SimplePagedResultsControl],
) -> bool:
    """
    Push latest cookie back into the page control.
    """

    cookie = pctrls[0].cookie
    lc_object.cookie = cookie
    return bool(cookie)


class LDAPSourceConfig(ConfigModel):
    """Config used by the LDAP Source."""

    # Server configuration.
    ldap_server: str = Field(description="LDAP server URL.")
    ldap_user: str = Field(description="LDAP user.")
    ldap_password: str = Field(description="LDAP password.")

    # Extraction configuration.
    base_dn: str = Field(description="LDAP DN.")
    filter: str = Field(default="(objectClass=*)", description="LDAP extractor filter.")

    # If set to true, any users without first and last names will be dropped.
    drop_missing_first_last_name: bool = Field(
        default=True,
        description="If set to true, any users without first and last names will be dropped.",
    )

    page_size: int = Field(
        default=20, description="Size of each page to fetch when extracting metadata."
    )

    # default mapping for attrs
    user_attrs_map: Dict[str, Any] = {}
    group_attrs_map: Dict[str, Any] = {}


@dataclasses.dataclass
class LDAPSourceReport(SourceReport):

    dropped_dns: List[str] = dataclasses.field(default_factory=list)

    def report_dropped(self, dn: str) -> None:
        self.dropped_dns.append(dn)


def guess_person_ldap(
    attrs: Dict[str, Any], config: LDAPSourceConfig, report: LDAPSourceReport
) -> Optional[str]:
    """Determine the user's LDAP based on the DN and attributes."""
    if config.user_attrs_map["urn"] in attrs:
        return attrs[config.user_attrs_map["urn"]][0].decode()
    else:  # for backward compatiblity
        if "sAMAccountName" in attrs:
            report.report_warning(
                "<general>",
                "Defaulting to sAMAccountName as it was found in attrs and not set in user_attrs_map in recipe",
            )
            return attrs["sAMAccountName"][0].decode()
        if "uid" in attrs:
            report.report_warning(
                "<general>",
                "Defaulting to uid as it was found in attrs and not set in user_attrs_map in recipe",
            )
            return attrs["uid"][0].decode()
        return None


@platform_name("LDAP")
@config_class(LDAPSourceConfig)
@support_status(SupportStatus.CERTIFIED)
@dataclasses.dataclass
class LDAPSource(Source):
    """
    This plugin extracts the following:
    - People
    - Names, emails, titles, and manager information for each person
    - List of groups
    """

    config: LDAPSourceConfig
    report: LDAPSourceReport

    def __init__(self, ctx: PipelineContext, config: LDAPSourceConfig):
        """Constructor."""
        super().__init__(ctx)
        self.config = config
        # ensure prior defaults are in place
        for k in user_attrs_map:
            if k not in self.config.user_attrs_map:
                self.config.user_attrs_map[k] = user_attrs_map[k]

        for k in group_attrs_map:
            if k not in self.config.group_attrs_map:
                self.config.group_attrs_map[k] = group_attrs_map[k]

        self.report = LDAPSourceReport()

        ldap.set_option(ldap.OPT_X_TLS_REQUIRE_CERT, ldap.OPT_X_TLS_ALLOW)
        ldap.set_option(ldap.OPT_REFERRALS, 0)

        self.ldap_client = ldap.initialize(self.config.ldap_server)
        self.ldap_client.protocol_version = 3

        try:
            self.ldap_client.simple_bind_s(
                self.config.ldap_user, self.config.ldap_password
            )
        except ldap.LDAPError as e:
            raise ConfigurationError("LDAP connection failed") from e

        self.lc = create_controls(self.config.page_size)

    @classmethod
    def create(cls, config_dict: Dict[str, Any], ctx: PipelineContext) -> "LDAPSource":
        """Factory method."""
        config = LDAPSourceConfig.parse_obj(config_dict)
        return cls(ctx, config)

    def get_workunits(self) -> Iterable[MetadataWorkUnit]:
        """Returns an Iterable containing the workunits to ingest LDAP users or groups."""
        cookie = True
        while cookie:
            try:
                msgid = self.ldap_client.search_ext(
                    self.config.base_dn,
                    ldap.SCOPE_SUBTREE,
                    self.config.filter,
                    serverctrls=[self.lc],
                )
                _rtype, rdata, _rmsgid, serverctrls = self.ldap_client.result3(msgid)
            except ldap.LDAPError as e:
                self.report.report_failure(
                    "ldap-control", "LDAP search failed: {}".format(e)
                )
                break

            for dn, attrs in rdata:
                if dn is None:
                    continue

                if not attrs:
                    self.report.report_warning(
                        "<general>",
                        f"skipping {dn} because attrs is empty; check your permissions if this is unexpected",
                    )
                    continue

                if (
                    b"inetOrgPerson" in attrs["objectClass"]
                    or b"posixAccount" in attrs["objectClass"]
                    or b"person" in attrs["objectClass"]
                ):
                    yield from self.handle_user(dn, attrs)
                elif (
                    b"posixGroup" in attrs["objectClass"]
                    or b"organizationalUnit" in attrs["objectClass"]
                    or b"group" in attrs["objectClass"]
                ):
                    yield from self.handle_group(dn, attrs)
                else:
                    self.report.report_dropped(dn)

            pctrls = get_pctrls(serverctrls)
            if not pctrls:
                self.report.report_failure(
                    "ldap-control", "Server ignores RFC 2696 control."
                )
                break

            cookie = set_cookie(self.lc, pctrls)

    def handle_user(self, dn: str, attrs: Dict[str, Any]) -> Iterable[MetadataWorkUnit]:
        """
        Handle a DN and attributes by adding manager info and constructing a
        work unit based on the information.
        """
        manager_ldap = None
        if self.config.user_attrs_map["managerUrn"] in attrs:
            try:
                m_cn = attrs[self.config.user_attrs_map["managerUrn"]][0].decode()
                manager_msgid = self.ldap_client.search_ext(
                    m_cn,
                    ldap.SCOPE_BASE,
                    self.config.filter,
                    serverctrls=[self.lc],
                )
                result = self.ldap_client.result3(manager_msgid)
                if result[1]:
                    _m_dn, m_attrs = result[1][0]
                    manager_ldap = guess_person_ldap(m_attrs, self.config, self.report)
            except ldap.LDAPError as e:
                self.report.report_warning(
                    dn, "manager LDAP search failed: {}".format(e)
                )

        mce = self.build_corp_user_mce(dn, attrs, manager_ldap)
        if mce:
            wu = MetadataWorkUnit(dn, mce)
            self.report.report_workunit(wu)
            yield wu
        else:
            self.report.report_dropped(dn)

    def handle_group(
        self, dn: str, attrs: Dict[str, Any]
    ) -> Iterable[MetadataWorkUnit]:
        """Creates a workunit for LDAP groups."""

        mce = self.build_corp_group_mce(attrs)
        if mce:
            wu = MetadataWorkUnit(dn, mce)
            self.report.report_workunit(wu)
            yield wu
        else:
            self.report.report_dropped(dn)

    def build_corp_user_mce(
        self, dn: str, attrs: dict, manager_ldap: Optional[str]
    ) -> Optional[MetadataChangeEvent]:
        """
        Create the MetadataChangeEvent via DN and attributes.
        """
        ldap_user = guess_person_ldap(attrs, self.config, self.report)

        if self.config.drop_missing_first_last_name and (
            self.config.user_attrs_map["firstName"] not in attrs
            or self.config.user_attrs_map["lastName"] not in attrs
        ):
            return None
        full_name = attrs[self.config.user_attrs_map["fullName"]][0].decode()
        first_name = attrs[self.config.user_attrs_map["firstName"]][0].decode()
        last_name = attrs[self.config.user_attrs_map["lastName"]][0].decode()

        email = (
            (attrs[self.config.user_attrs_map["email"]][0]).decode()
            if self.config.user_attrs_map["email"] in attrs
            else ldap_user
        )
        display_name = (
            (attrs[self.config.user_attrs_map["displayName"]][0]).decode()
            if self.config.user_attrs_map["displayName"] in attrs
            else full_name
        )
        department_id = (
            int(attrs[self.config.user_attrs_map["departmentId"]][0].decode())
            if self.config.user_attrs_map["departmentId"] in attrs
            else None
        )
        department_name = (
            (attrs[self.config.user_attrs_map["departmentName"]][0]).decode()
            if self.config.user_attrs_map["departmentName"] in attrs
            else None
        )
        country_code = (
            (attrs[self.config.user_attrs_map["countryCode"]][0]).decode()
            if self.config.user_attrs_map["countryCode"] in attrs
            else None
        )
        title = (
            attrs[self.config.user_attrs_map["title"]][0].decode()
            if self.config.user_attrs_map["title"] in attrs
            else None
        )
        manager_urn = f"urn:li:corpuser:{manager_ldap}" if manager_ldap else None

        return MetadataChangeEvent(
            proposedSnapshot=CorpUserSnapshotClass(
                urn=f"urn:li:corpuser:{ldap_user}",
                aspects=[
                    CorpUserInfoClass(
                        active=True,
                        email=email,
                        fullName=full_name,
                        firstName=first_name,
                        lastName=last_name,
                        departmentId=department_id,
                        departmentName=department_name,
                        displayName=display_name,
                        countryCode=country_code,
                        title=title,
                        managerUrn=manager_urn,
                    )
                ],
            )
        )

    def build_corp_group_mce(self, attrs: dict) -> Optional[MetadataChangeEvent]:
        """Creates a MetadataChangeEvent for LDAP groups."""
        cn = attrs.get(self.config.group_attrs_map["urn"])
        if cn:
            full_name = cn[0].decode()
            admins = parse_from_attrs(attrs, self.config.group_attrs_map["admins"])
            members = parse_from_attrs(attrs, self.config.group_attrs_map["members"])
            email = (
                attrs[self.config.group_attrs_map["email"]][0].decode()
                if self.config.group_attrs_map["email"] in attrs
                else full_name
            )
            description = (
                attrs[self.config.group_attrs_map["description"]][0].decode()
                if self.config.group_attrs_map["description"] in attrs
                else None
            )
            displayName = (
                attrs[self.config.group_attrs_map["displayName"]][0].decode()
                if self.config.group_attrs_map["displayName"] in attrs
                else None
            )
            return MetadataChangeEvent(
                proposedSnapshot=CorpGroupSnapshotClass(
                    urn=f"urn:li:corpGroup:{full_name}",
                    aspects=[
                        CorpGroupInfoClass(
                            email=email,
                            admins=admins,
                            members=members,
                            groups=[],
                            description=description,
                            displayName=displayName,
                        )
                    ],
                )
            )
        return None

    def get_report(self) -> LDAPSourceReport:
        """Returns the source report."""
        return self.report

    def close(self) -> None:
        """Closes the Source."""
        self.ldap_client.unbind()


def parse_from_attrs(attrs: Dict[str, Any], filter_key: str) -> List[str]:
    """Converts a list of LDAP formats to Datahub corpuser strings."""
    if filter_key in attrs:
        return [
            f"urn:li:corpuser:{strip_ldap_info(ldap_user)}"
            for ldap_user in attrs[filter_key]
        ]
    return []


def strip_ldap_info(input_clean: bytes) -> str:
    """Converts a b'uid=username,ou=Groups,dc=internal,dc=machines'
    format to username"""
    return input_clean.decode().split(",")[0].lstrip("uid=")
