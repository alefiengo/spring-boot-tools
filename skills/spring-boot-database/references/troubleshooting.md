# Database troubleshooting

Use for a reported database incident after identifying the actual database,
environment and available diagnostics. Commands below are PostgreSQL examples;
inspect existing configuration and obtain execution authorization where needed.


### Connection

```bash
# Direct connection test from the host with psql/pg_isready:
pg_isready -h localhost -p 5432

# Inspect the connection pool config in the app
curl http://localhost:8080/actuator/env/spring.datasource.hikari.maximum-pool-size
```

### Failed migrations

The Maven Flyway commands require a configured Flyway Maven plugin and database connection; the Boot starter alone does not configure CLI/plugin credentials. Do not print credentials in diagnostic output.

```bash
# Check migration status
./mvnw flyway:info
# Or if you're in prod
flyway info

# Investigate validation/history and restore the original applied script.
# Apply corrections in a new migration; do not repair merely to silence a checksum mismatch.
```

### Locks and hung connections

```sql
SELECT pid, state, query_start, wait_event, query
FROM pg_stat_activity
WHERE state != 'idle'
  AND query NOT ILIKE '%pg_stat%';
```

### Slow queries

```yaml
# Enable slow query logging in application-local.yml
logging:
  level:
    org.hibernate.SQL: DEBUG
    org.hibernate.stat: DEBUG
```

Better yet, keep `spring.jpa.show-sql: false` and use `pg_stat_statements` in PostgreSQL for production:

```sql
SELECT query, calls, total_exec_time / NULLIF(calls, 0) AS avg_ms
FROM pg_stat_statements
ORDER BY total_exec_time DESC
LIMIT 10;
```
