---
type: llm
---

PASS only if it uses spring-boot-starter-opentelemetry and management.opentelemetry.tracing.export.otlp.endpoint, and the filter uses HttpServletRequest/HttpServletResponse directly or via OncePerRequestFilter with IOException/ServletException handled or declared. FAIL for top-level tracing.otlp or getHeader/setHeader on generic ServletRequest/ServletResponse.
