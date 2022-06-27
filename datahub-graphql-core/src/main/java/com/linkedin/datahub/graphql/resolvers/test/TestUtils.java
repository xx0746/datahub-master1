package com.linkedin.datahub.graphql.resolvers.test;

import com.linkedin.data.template.SetMode;
import com.linkedin.datahub.graphql.QueryContext;
import com.linkedin.datahub.graphql.generated.TestDefinitionInput;
import com.linkedin.metadata.authorization.PoliciesConfig;
import com.linkedin.test.TestDefinition;
import com.linkedin.test.TestDefinitionType;
import java.util.Optional;
import javax.annotation.Nonnull;

import static com.linkedin.datahub.graphql.authorization.AuthorizationUtils.*;


public class TestUtils {

  /**
   * Returns true if the authenticated user is able to manage tests.
   */
  public static boolean canManageTests(@Nonnull QueryContext context) {
    return isAuthorized(context, Optional.empty(), PoliciesConfig.MANAGE_TESTS_PRIVILEGE);
  }

  public static TestDefinition mapDefinition(final TestDefinitionInput testDefInput) {
    final TestDefinition result = new TestDefinition();
    result.setType(TestDefinitionType.JSON); // Always JSON for now.
    result.setJson(testDefInput.getJson(), SetMode.IGNORE_NULL);
    return result;
  }

  private TestUtils() { }
}
