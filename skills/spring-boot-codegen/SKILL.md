---
name: spring-boot-codegen
description: >
  Guides generation of idiomatic Spring Boot code (Spring Boot 4.x / Java 25+):
  JPA entities, REST controllers, services, repositories, DTOs, configuration,
  and project-specific architecture conventions. Use when asked to create
  endpoints, entities, DTOs, mappers, configurations, or any new Spring Boot
  project code.
---

# Spring Boot Code Generation

> Read the project build first. For a new project, use a supported **Spring Boot 4.x** release and **Java 25 LTS** unless the user chooses another compatible target. Boot 4 uses Framework 7, Jackson 3 and JSpecify; adapt imports and APIs for existing Boot 3 projects.

Guides code generation following idiomatic Spring Boot conventions.

## Activation

This skill loads automatically when you ask to generate entities, REST endpoints, services, repositories, DTOs, configurations, or any new Spring Boot code.

## Layer conventions

```
src/main/java/com/example/<project>/
├── domain/
│   ├── model/       → JPA entities, value objects
│   ├── repository/  → repository interfaces
│   └── usecase/     → domain services (pure logic)
├── application/
│   ├── dto/         → request/response DTOs
│   ├── service/     → application services (Spring @Service, orchestrates use cases)
│   └── security/    → @Secured annotations, roles
└── infrastructure/
    ├── config/      → @Configuration classes
    ├── mapper/      → DTO ↔ Domain mappers
    └── security/    → JWT filters, auth providers
```

### Exceptions

- Place method-security annotations on the secured controller/service method. Enable `@EnableMethodSecurity`; `@Secured` additionally needs `securedEnabled=true`. Keep reusable authorization components in `infrastructure/security/`.
- External service clients (HTTP interface clients): `infrastructure/client/`.

## Generation rules

### 0. Package-level null safety

For new Boot 4 code using JSpecify, a package can declare `package-info.java` with `@NullMarked`; use `@Nullable` explicitly where `null` is allowed. Do not use the deprecated `org.springframework.lang` nullability annotations.

```java
// src/main/java/com/example/<project>/domain/model/package-info.java
@NullMarked
package com.example.<project>.domain.model;

import org.jspecify.annotations.NullMarked;
```

### 1. Domain model (data-oriented programming)

For domain states where alternatives matter, a sealed interface + record hierarchy can make invalid combinations explicit. Model state explicitly and use pattern matching for domain decisions:

```java
public sealed interface OrderState permits Pending, Paid, Shipped, Cancelled {}

public record Pending(Instant createdAt) implements OrderState {}
public record Paid(Instant paidAt, Money amount) implements OrderState {}
public record Shipped(Instant shippedAt, String trackingCode) implements OrderState {}
public record Cancelled(Instant cancelledAt, String reason) implements OrderState {}
```

Domain logic as a `switch` pattern match (exhaustive, no `default` needed with sealed types):

```java
public String describe(OrderState state) {
    return switch (state) {
        case Pending p                 -> "created at " + p.createdAt();
        case Paid p when p.amount().isZero() -> "paid for free";
        case Paid p                    -> "paid " + p.amount();
        case Shipped s                 -> "shipped, tracking " + s.trackingCode();
        case Cancelled(var at, _)      -> "cancelled at " + at; // record pattern, unnamed variable
    };
}
```

Use the JPA entity pattern below only for persistence-backed aggregates; keep the record domain model as the language for use-case logic and map between them in the mapper layer.

### 2. Persistence-backed model

When generating a persistence-backed aggregate, preserve the existing entity access strategy, identity and schema constraints. Use [database](../spring-boot-database/SKILL.md) for complete mapping, auditing and migration examples; keep pure domain records separate from JPA entities where the project already does so.

### 3. REST controllers

- Naming: entity in English, plural, hyphens. `GET /api/v1/users`
- Use `@RestController`, `@RequestMapping("${api.base-path}/v1/<resource>")`
- DTOs in request parameters, never JPA entities.
- Pagination: accept `page` (0-based) and `size`, return `Page<DTO>` wrapped with metadata.

```java
@RestController
@RequestMapping("${api.base-path}/v1/users")
@RequiredArgsConstructor
public class UserController {
    private final UserService userService;

    @GetMapping
    public ResponseEntity<Page<UserDTO>> search(
            @RequestParam(defaultValue = "0") int page,
            @RequestParam(defaultValue = "20") int size,
            UserFilter filter) { ... }

    @PostMapping
    public ResponseEntity<UserDTO> create(@Valid @RequestBody CreateUserDTO dto) { ... }

    @GetMapping("/{id}")
    public ResponseEntity<UserDTO> get(@PathVariable Long id) { ... }
}
```

### 4. Repositories

Extend `JpaRepository<Entity, Long>` or use an interface with `@Query` for complex cases.

```java
public interface UserRepository extends JpaRepository<User, Long> {
    Optional<User> findByEmail(String email);
    boolean existsByEmail(String email);
}
```

### 5. DTOs with validation

Use Bean Validation (`jakarta.validation.constraints`). Do not repeat Arrays → `List<>`.

```java
public record CreateUserDTO(
    @NotBlank @Size(max = 100) String name,
    @Email @NotBlank String email,
    @NotNull @Min(1) @Max(120) Integer age
) {}
```

## Scope and delivery

Inspect existing packages, build targets and neighbouring classes before generation. The layout and controller above are examples; preserve the project's architecture and public contract. Generate the smallest complete change, including imports, constructors and required collaborators, rather than copying placeholder classes.

Use [database](../spring-boot-database/SKILL.md) for persistence mapping and migrations, [API contracts](../spring-boot-api-contracts/SKILL.md) for response design, [security hardening](../spring-boot-security-hardening/SKILL.md) when auth/security changes are requested, and [testing](../spring-boot-testing/SKILL.md) for verification. Match existing authentication and authorization without introducing a new mechanism merely to generate an endpoint.

Deliver files changed, behavior added and the appropriate compile/test result. Reuse an existing verification of the same source state; do not launch another build just to repeat its result.

## References

- [Method security](https://docs.spring.io/spring-security/reference/servlet/authorization/method-security.html)
- [JPA auditing](https://docs.spring.io/spring-data/jpa/reference/auditing.html)
