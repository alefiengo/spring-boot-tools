---
type: llm
---

PASS only if postgres:18-alpine is used, the named volume is mounted at /var/lib/postgresql, and the answer explains the PostgreSQL 18 data directory change. FAIL if it mounts the named volume solely at /var/lib/postgresql/data without explicitly setting a compatible PGDATA.
