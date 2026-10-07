---
description: Tests assert observable risks using supplied source and fixtures
max_turns: 10
allowed_tools: [Read, Glob, Grep, Skill]
---

Write proposed tests for this order approval service. It must reject access to another user's order, preserve the existing JSON error fields, and roll back when persistence fails. Separate unit, security integration, and database integration coverage. Reuse the supplied FixtureData, schema, and ownership error JSON; they are actual versioned evaluation fixtures, fully included below because your working directory may be empty.

The intended HTTP operation is POST /orders/101/approve. The authenticated subject supplies currentUserId; the client must not be able to choose an owner's identity. The error JSON below is the existing compatibility contract. HTTP routing, security configuration, exception advice, build configuration, and a database test harness are deliberately not supplied. Identify these missing prerequisites for runnable HTTP/database tests instead of inventing their existing implementation or claiming execution. Proposed test code plus concrete integration assertions and setup are acceptable. Target the project's PostgreSQL database for rollback verification; a mock interaction or a test-level rollback does not demonstrate the service transaction.

Use bob authenticated against alice's seeded order as an ownership-negative case, and alice as the authorized positive case. For the persistence-failure case, FAIL_DB violates the supplied SQL constraint after markApproved has updated the row. Verify persisted state from a separate transaction after calling the Spring-managed service; otherwise a surrounding test transaction can hide a missing service transaction.

The following files are the complete supplied fixture context. They are inputs for test design, not a claim that a runnable application or passing integration tests already exist.

### Fixture: fixtures/FixtureData.java

```java
package example.orders;

public final class FixtureData {
    public static final long ORDER_ID = 101;
    public static final String OWNER = "alice";
    public static final String OTHER_USER = "bob";
    public static final String SUCCESS_EVENT = "approved-101";
    public static final String FAILING_EVENT = "FAIL_DB";

    private FixtureData() {}

    public static OrderService.Order pendingOrder() {
        return new OrderService.Order(ORDER_ID, OWNER, "PENDING");
    }
}
```

### Fixture: fixtures/OrderRepository.java

```java
package example.orders;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

@Repository
public class OrderRepository {
    private final JdbcTemplate jdbc;

    public OrderRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    public OrderService.Order find(long id) {
        return jdbc.queryForObject(
            "select id, owner_id, status from orders where id = ?",
            (row, index) -> new OrderService.Order(
                row.getLong("id"), row.getString("owner_id"), row.getString("status")), id);
    }

    public void markApproved(long id) {
        jdbc.update("update orders set status = 'APPROVED' where id = ?", id);
    }

    public void appendEvent(long orderId, String eventId) {
        jdbc.update("insert into order_events(order_id, event_id) values (?, ?)", orderId, eventId);
    }
}
```

### Fixture: fixtures/OrderService.java

```java
package example.orders;

import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class OrderService {
    private final OrderRepository orders;

    public OrderService(OrderRepository orders) {
        this.orders = orders;
    }

    @Transactional
    public void approve(long orderId, String currentUserId, String eventId) {
        Order order = orders.find(orderId);
        if (!order.ownerId().equals(currentUserId)) {
            throw new OwnershipDenied();
        }
        orders.markApproved(orderId);
        // The fixture's FAIL_DB event violates a real database constraint.
        orders.appendEvent(orderId, eventId);
    }

    public record Order(long id, String ownerId, String status) {}
    public static class OwnershipDenied extends RuntimeException {}
}
```

### Fixture: fixtures/ownership-error.json

```json
{"status":403,"code":"ORDER_ACCESS_DENIED","message":"You cannot access this order","path":"/orders/101/approve"}
```

### Fixture: fixtures/schema.sql

```sql
CREATE TABLE orders (
    id BIGINT PRIMARY KEY,
    owner_id VARCHAR(100) NOT NULL,
    status VARCHAR(20) NOT NULL
);
CREATE TABLE order_events (
    order_id BIGINT NOT NULL REFERENCES orders(id),
    event_id VARCHAR(100) NOT NULL UNIQUE,
    CONSTRAINT reject_failure_fixture CHECK (event_id <> 'FAIL_DB')
);
INSERT INTO orders(id, owner_id, status) VALUES (101, 'alice', 'PENDING');
```
