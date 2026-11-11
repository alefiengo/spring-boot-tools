---
description: Explicitly requested test-writer specialist covers an observable regression
max_turns: 12
allowed_tools: [Read, Glob, Grep, Skill, Agent]
---

Use the sb-test-writer agent for this. In our Spring Boot service, DiscountService.apply returned a negative total when the discount exceeded the order total; the fix below clamps the result at zero. Propose a regression test that would fail before the fix and pass after it, plus the boundary case where the discount equals the total. The working directory may be empty, so the source is included here; do not claim the tests were run.

```java
package example.pricing;

import java.math.BigDecimal;
import org.springframework.stereotype.Service;

@Service
public class DiscountService {
    public BigDecimal apply(BigDecimal total, BigDecimal discount) {
        BigDecimal result = total.subtract(discount);
        return result.signum() < 0 ? BigDecimal.ZERO : result;
    }
}
```
