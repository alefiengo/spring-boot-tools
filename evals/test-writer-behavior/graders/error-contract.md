---
type: llm
---

PASS if the proposed HTTP assertions preserve the supplied ownership-error.json contract: status 403, code ORDER_ACCESS_DENIED, message "You cannot access this order", and path /orders/101/approve. It is acceptable to assert an expected JSON fixture containing these values instead of listing them again. FAIL if only the HTTP status is asserted or existing JSON fields are changed or omitted. A proposed test need not be runnable without the missing HTTP/security/advice configuration. Explain the verdict with evidence from the response.
