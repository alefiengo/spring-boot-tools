---
type: llm
---

PASS if image publication is a separate job that depends on the verification job and runs only for main, never for pull requests. FAIL if a pull request can publish an image or publication can run when verification failed.
