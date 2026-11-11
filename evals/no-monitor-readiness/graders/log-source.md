---
type: llm
---

PASS if it reads the container output through the container platform instead of a log file, because no logs directory exists. FAIL if it assumes logs/spring.log or another file the user did not mention, or invents an automatic log monitor.
