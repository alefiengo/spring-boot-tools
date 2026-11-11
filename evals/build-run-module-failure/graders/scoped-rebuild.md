---
type: llm
---

PASS if the answer rebuilds the selected module with the modules it depends on instead of repeatedly cleaning and rebuilding the whole reactor, and does not propose skipping tests as the fix. FAIL if its main remedy is a repeated full clean build or skipping tests.
