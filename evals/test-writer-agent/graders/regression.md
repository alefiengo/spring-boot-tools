---
type: llm
---

PASS if the proposed tests call DiscountService.apply with a discount greater than the total and assert a result of zero, and also cover a discount equal to the total with a zero result. FAIL if the tests only verify mock interactions, recompute the expected value with the same subtraction logic as the production code, or omit the discount-greater-than-total case.
