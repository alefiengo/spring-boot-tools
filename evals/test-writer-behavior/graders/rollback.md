---
type: llm
---

PASS if rollback verification calls the Spring-managed service with the supplied FAIL_DB constraint failure after the order update and then checks PENDING status and absence of an inserted event in PostgreSQL from a separate transaction. It must prevent a surrounding test transaction from masking a missing service transaction. Concrete integration setup/assertions are acceptable because no database test harness was supplied. FAIL if mock interactions, a thrown exception alone, or automatic rollback of the test transaction are claimed to prove service rollback. Explain the verdict with evidence from the response.
