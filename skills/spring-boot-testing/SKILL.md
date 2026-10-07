---
name: spring-boot-testing
description: >
  Guides writing and running tests in Spring Boot projects: unit tests with
  JUnit 5 + AssertJ + Mockito, @WebMvcTest for controllers, @DataJpaTest for
  repositories, @SpringBootTest + Testcontainers for integration, and coverage.
  Use when asked for tests, coverage, mocking, or flakiness.
---

# Testing in Spring Boot

Guides writing and running tests in Spring Boot projects with Maven/Gradle.

## Verification ownership

Choose tests from behavior and risk: unit for pure logic, slices for framework boundaries, integration for persistence/security wiring, contracts for independently released services. Reuse `run_tests` and its fresh reports; coordinate with hooks and PR review so unchanged sources are verified once. The test-writer agent should deliver observable assertions, not tests mirroring implementation details.


## Activation

Activates when asked to generate tests, or when discussing coverage, mocking, integration, or when you say "test this <component>".

## Default stack

| Layer           | Framework                        | Mocking            |
|-----------------|----------------------------------|--------------------|
| Unit tests      | JUnit 5 + AssertJ                | Mockito            |
| Repository      | `@DataJpaTest`                   | —                  |
| Controller      | `@WebMvcTest`                    | REST Test Client / MockMvc + Mockito |
| Integration     | `@SpringBootTest`                | Testcontainers     |
| Contract/API    | Spring Cloud Contract or WireMock | —                 |

Virtual threads are opt-in with `spring.threads.virtual.enabled=true`. Test executor configuration is separate; exercise concurrency-sensitive behavior with the same setting used in production.

For Boot 4 MVC/JPA slices add the relevant test-scoped modular starters (`spring-boot-starter-webmvc-test`, `spring-boot-starter-data-jpa-test`) alongside the project test setup. Testcontainers integration needs `spring-boot-testcontainers`, `org.testcontainers:testcontainers-junit-jupiter` and `org.testcontainers:testcontainers-postgresql` under Boot-managed versions. Import Boot 4 slice annotations from `org.springframework.boot.webmvc.test.autoconfigure` / `org.springframework.boot.data.jpa.test.autoconfigure`; do not copy Boot 3 package names.

## Conventions

### 1. Folder structure

Mirror the source: `src/test/java/com/example/<project>/...`

### 2. Method naming

```
should<Expectation>_when<Condition>
```

Example:
```java
@Test
void shouldReturnUser_whenValidId() { ... }

@Test
void shouldThrowNotFound_whenIdDoesNotExist() { ... }
```

### 3. Three A's pattern (Arrange-Act-Assert)

```java
// arrange
var user = new User("fixture@test.com");

// act
var result = userService.findByEmail("fixture@test.com");

// assert
assertThat(result).isPresent();
assertThat(result.get().email()).isEqualTo("fixture@test.com");
```

Use **AssertJ** fluent assertions, never `Assert.assertEquals`.

### 4. Repository & JPA tests (`@DataJpaTest`)

```java
@DataJpaTest
class UserRepositoryTest {

    @Autowired private UserRepository repository;
    @Autowired private TestEntityManager em;

    @Test
    void shouldPersistAndFindByEmail() {
        var user = em.persistFlushFind(new User("alice@test.com", "Alice"));

        var found = repository.findByEmail("alice@test.com");

        assertThat(found).isPresent();
        assertThat(found.get().getName()).isEqualTo("Alice");
    }
}
```

### 5. Controller tests (`@WebMvcTest`)

Use `MockMvc` for MVC slices. For a running-server integration test, use `@SpringBootTest(webEnvironment = RANDOM_PORT)` plus `@AutoConfigureRestTestClient` and Boot's REST test-client test starter. Do not assume a RestTestClient bean in a `@WebMvcTest` slice.

```java
@WebMvcTest(UserController.class)
class UserControllerMockMvcTest {

    @Autowired private MockMvc mockMvc;

    @MockitoBean private UserService userService;

    @Test
    void shouldReturn200_whenSearch() throws Exception {
        given(userService.search(any())).willReturn(Page.empty());

        mockMvc.perform(get("/api/v1/users?size=10"))
               .andExpect(status().isOk())
               .andExpect(jsonPath("$.content").isArray());
    }
}
```

### 6. Integration tests (`@SpringBootTest` + Testcontainers)

Use Testcontainers for a real database (PostgreSQL). Use the JUnit Testcontainers extension with static `@Container` fields for class lifecycle.

```java
@SpringBootTest(webEnvironment = RANDOM_PORT)
@Testcontainers
class UserIntegrationTest {

    @Container
    static PostgreSQLContainer postgres = new PostgreSQLContainer("postgres:18-alpine");

    @DynamicPropertySource
    static void datasourceProps(DynamicPropertyRegistry reg) {
        reg.add("spring.datasource.url", postgres::getJdbcUrl);
        reg.add("spring.datasource.username", postgres::getUsername);
        reg.add("spring.datasource.password", postgres::getPassword);
    }

    @Test
    void shouldCreateAndRetrieveUser() { ... }
}
```

Alternatively annotate the container with Boot `@ServiceConnection` and add `spring-boot-testcontainers`; that supplies connection details without `@DynamicPropertySource`. With Testcontainers 2, use `org.testcontainers.postgresql.PostgreSQLContainer` (no generic parameter) and the `testcontainers-postgresql` module; 1.x projects instead use `org.testcontainers.containers.PostgreSQLContainer<?>`.

### 7. Coverage (command)

These commands assume the project defines the coverage profile/property and a JaCoCo verification rule. Neither tool enforces `minCoverage` automatically. Configure the rule first.

```bash
# Maven
./mvnw verify -P coverage -DminCoverage=0.8

# Gradle
./gradlew check -DminCoverage=0.8
```

## AI test generation

Rules for Claude when generating a test:
1. Each test class covers a single class under test.
2. At minimum: a positive test, a boundary test (null, empty), and an error test.
3. Name fixture helpers `aUser()`, `anAdmin()`.
4. Build DTOs inline or with builders.
5. Do not mock configurations or property sources unless they are external.

## References

- [Boot Testcontainers service connections](https://docs.spring.io/spring-boot/reference/testing/testcontainers.html)
- [Boot MVC test configuration](https://docs.spring.io/spring-boot/reference/testing/spring-boot-applications.html)
