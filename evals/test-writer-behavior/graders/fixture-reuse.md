---
type: llm
---

PASS if test code or explicit setup/assertion plans reuse the supplied FixtureData identities/order, schema.sql database seed and failure constraint, and ownership-error.json expected error contract. References by filename or concrete contents count as reuse; the files need not be found on disk because all their contents were supplied in the prompt. FAIL if the response ignores these supplied fixtures and substitutes incompatible identities, seed state, failure semantics, or error fields. Explain the verdict with evidence from the response.
