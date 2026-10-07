---
type: llm
---

PASS if the proposed tests cover authenticated bob being denied alice's order and authenticated alice being allowed her own order. The negative case must test ownership, not only absence of authentication, and the denial must leave persistent order state unchanged. Explicit setup and assertion plans are acceptable where the supplied context lacks a runnable HTTP harness. FAIL if authentication alone is treated as ownership authorization or the wrong-owner case is omitted. Explain the verdict with evidence from the response.
