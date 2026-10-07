---
type: llm
---

PASS only if the answer preserves V7 unchanged, creates a new versioned ALTER migration for the new column, and explains that repair/checksum changes do not execute the schema change. FAIL if it recommends renaming or editing V7 followed by repair as the deployment strategy.
