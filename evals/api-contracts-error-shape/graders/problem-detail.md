---
type: llm
---

PASS if the contract uses RFC 9457 problem details (type, title, status, detail, instance) for both errors and represents field validation errors as a structured list rather than a single concatenated string. FAIL if it defines an ad hoc error shape without RFC 9457 or exposes stack traces.
