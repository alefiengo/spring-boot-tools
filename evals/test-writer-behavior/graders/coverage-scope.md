---
type: llm
---

PASS if the response separates unit checks, authenticated HTTP/security integration checks, and real database transaction integration checks, stating missing prerequisites for the latter where needed. Assertions must address observable authorization, JSON compatibility, or persisted state; verifying implementation calls alone cannot establish correctness. FAIL if all three risks are assigned to mock-only unit tests or absent configuration is invented and presented as existing/runnable. Explain the verdict with evidence from the response.
