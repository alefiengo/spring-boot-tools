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
