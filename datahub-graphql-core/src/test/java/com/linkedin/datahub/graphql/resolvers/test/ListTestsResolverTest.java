package com.linkedin.datahub.graphql.resolvers.test;

import com.datahub.authentication.Authentication;
import com.google.common.collect.ImmutableSet;
import com.linkedin.common.urn.Urn;
import com.linkedin.datahub.graphql.QueryContext;
import com.linkedin.datahub.graphql.generated.ListTestsInput;
import com.linkedin.entity.client.EntityClient;
import com.linkedin.metadata.Constants;
import com.linkedin.metadata.search.SearchEntity;
import com.linkedin.metadata.search.SearchEntityArray;
import com.linkedin.metadata.search.SearchResult;
import com.linkedin.r2.RemoteInvocationException;
import graphql.schema.DataFetchingEnvironment;
import java.util.Collections;
import java.util.concurrent.CompletionException;
import org.mockito.Mockito;
import org.testng.annotations.Test;

import static com.linkedin.datahub.graphql.TestUtils.*;
import static org.testng.Assert.*;


public class ListTestsResolverTest {

  private static final Urn TEST_URN = Urn.createFromTuple("test", "test-id");

  private static final ListTestsInput TEST_INPUT = new ListTestsInput(
      0, 20, null
  );

  @Test
  public void testGetSuccess() throws Exception {
    // Create resolver
    EntityClient mockClient = Mockito.mock(EntityClient.class);

    Mockito.when(mockClient.search(
        Mockito.eq(Constants.TEST_ENTITY_NAME),
        Mockito.eq(""),
        Mockito.eq(Collections.emptyMap()),
        Mockito.eq(0),
        Mockito.eq(20),
        Mockito.any(Authentication.class)
    )).thenReturn(
        new SearchResult()
            .setFrom(0)
            .setPageSize(1)
            .setNumEntities(1)
            .setEntities(new SearchEntityArray(ImmutableSet.of(new SearchEntity().setEntity(TEST_URN))))
    );

    ListTestsResolver resolver = new ListTestsResolver(mockClient);

    // Execute resolver
    QueryContext mockContext = getMockAllowContext();
    DataFetchingEnvironment mockEnv = Mockito.mock(DataFetchingEnvironment.class);
    Mockito.when(mockEnv.getArgument(Mockito.eq("input"))).thenReturn(TEST_INPUT);
    Mockito.when(mockEnv.getContext()).thenReturn(mockContext);

    // Data Assertions
    assertEquals((int) resolver.get(mockEnv).get().getStart(), 0);
    assertEquals((int) resolver.get(mockEnv).get().getCount(), 1);
    assertEquals((int) resolver.get(mockEnv).get().getTotal(), 1);
    assertEquals(resolver.get(mockEnv).get().getTests().size(), 1);
    assertEquals(resolver.get(mockEnv).get().getTests().get(0).getUrn(), TEST_URN.toString());
  }

  @Test
  public void testGetUnauthorized() throws Exception {
    // Create resolver
    EntityClient mockClient = Mockito.mock(EntityClient.class);
    ListTestsResolver resolver = new ListTestsResolver(mockClient);

    // Execute resolver
    DataFetchingEnvironment mockEnv = Mockito.mock(DataFetchingEnvironment.class);
    QueryContext mockContext = getMockDenyContext();
    Mockito.when(mockEnv.getArgument(Mockito.eq("input"))).thenReturn(
        TEST_INPUT);
    Mockito.when(mockEnv.getContext()).thenReturn(mockContext);

    assertThrows(CompletionException.class, () -> resolver.get(mockEnv).join());
    Mockito.verify(mockClient, Mockito.times(0)).search(
        Mockito.any(),
        Mockito.eq(""),
        Mockito.anyMap(),
        Mockito.anyInt(),
        Mockito.anyInt(),
        Mockito.any(Authentication.class));
  }

  @Test
  public void testGetEntityClientException() throws Exception {
    // Create resolver
    EntityClient mockClient = Mockito.mock(EntityClient.class);
    Mockito.doThrow(RemoteInvocationException.class).when(mockClient).search(
        Mockito.any(),
        Mockito.eq(""),
        Mockito.anyMap(),
        Mockito.anyInt(),
        Mockito.anyInt(),
        Mockito.any(Authentication.class));
    ListTestsResolver resolver = new ListTestsResolver(mockClient);

    // Execute resolver
    DataFetchingEnvironment mockEnv = Mockito.mock(DataFetchingEnvironment.class);
    QueryContext mockContext = getMockAllowContext();
    Mockito.when(mockEnv.getArgument(Mockito.eq("input"))).thenReturn(TEST_INPUT);
    Mockito.when(mockEnv.getContext()).thenReturn(mockContext);

    assertThrows(CompletionException.class, () -> resolver.get(mockEnv).join());
  }
}