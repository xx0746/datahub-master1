package com.linkedin.metadata.kafka;

import com.linkedin.gms.factory.telemetry.ScheduledAnalyticsFactory;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.autoconfigure.cassandra.CassandraAutoConfiguration;
import org.springframework.boot.autoconfigure.elasticsearch.ElasticsearchRestClientAutoConfiguration;
import org.springframework.context.annotation.ComponentScan;
import org.springframework.context.annotation.FilterType;


@SuppressWarnings("checkstyle:HideUtilityClassConstructor")
@SpringBootApplication(exclude = {ElasticsearchRestClientAutoConfiguration.class, CassandraAutoConfiguration.class})
@ComponentScan(excludeFilters = {
    @ComponentScan.Filter(type = FilterType.ASSIGNABLE_TYPE, classes = ScheduledAnalyticsFactory.class)})
public class MaeConsumerApplication {

  public static void main(String[] args) {
    Class<?>[] primarySources = {MaeConsumerApplication.class, MclConsumerConfig.class};
    SpringApplication.run(primarySources, args);
  }
}
