package com.linkedin.datahub.graphql.resolvers.test;

import com.linkedin.common.urn.UrnUtils;
import com.linkedin.data.template.SetMode;
import com.linkedin.datahub.graphql.QueryContext;
import com.linkedin.datahub.graphql.exception.AuthorizationException;
import com.linkedin.datahub.graphql.generated.UpdateTestInput;
import com.linkedin.entity.client.EntityClient;
import com.linkedin.events.metadata.ChangeType;
import com.linkedin.metadata.Constants;
import com.linkedin.metadata.utils.GenericRecordUtils;
import com.linkedin.mxe.MetadataChangeProposal;
import com.linkedin.test.TestInfo;
import graphql.schema.DataFetcher;
import graphql.schema.DataFetchingEnvironment;
import java.util.concurrent.CompletableFuture;

import static com.linkedin.datahub.graphql.resolvers.ResolverUtils.*;
import static com.linkedin.datahub.graphql.resolvers.test.TestUtils.*;


/**
 * Updates or updates a Test. Requires the MANAGE_TESTS privilege.
 */
public class UpdateTestResolver implements DataFetcher<CompletableFuture<String>> {

  private final EntityClient _entityClient;

  public UpdateTestResolver(final EntityClient entityClient) {
    _entityClient = entityClient;
  }

  @Override
  public CompletableFuture<String> get(final DataFetchingEnvironment environment) throws Exception {
    final QueryContext context = environment.getContext();

    return CompletableFuture.supplyAsync(() -> {

      if (canManageTests(context)) {

        final String urn = environment.getArgument("urn");
        final UpdateTestInput input = bindArgument(environment.getArgument("input"), UpdateTestInput.class);
        final MetadataChangeProposal proposal = new MetadataChangeProposal();

        // Update the Test info - currently this simply creates a new test with same urn.
        final TestInfo info = mapUpdateTestInput(input);
        proposal.setEntityUrn(UrnUtils.getUrn(urn));
        proposal.setEntityType(Constants.TEST_ENTITY_NAME);
        proposal.setAspectName(Constants.TEST_INFO_ASPECT_NAME);
        proposal.setAspect(GenericRecordUtils.serializeAspect(info));
        proposal.setChangeType(ChangeType.UPSERT);

        try {
          return _entityClient.ingestProposal(proposal, context.getAuthentication());
        } catch (Exception e) {
          throw new RuntimeException(String.format("Failed to perform update against Test with urn %s", input.toString()), e);
        }
      }
      throw new AuthorizationException("Unauthorized to perform this action. Please contact your DataHub administrator.");
    });
  }

  private static TestInfo mapUpdateTestInput(final UpdateTestInput input) {
    final TestInfo result = new TestInfo();
    result.setName(input.getName());
    result.setCategory(input.getCategory());
    result.setDescription(input.getDescription(), SetMode.IGNORE_NULL);
    result.setDefinition(mapDefinition(input.getDefinition()));
    return result;
  }
}
