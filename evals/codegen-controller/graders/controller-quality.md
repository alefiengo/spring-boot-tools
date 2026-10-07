---
type: llm
---

PASS if the response contains controller code where:
- The controller uses @RestController and a class-level @RequestMapping for the resource path.
- List endpoint accepts page/size request parameters and paginates (Page or PageResponse in the return type).
- The create endpoint takes a validated DTO (record with Bean Validation annotations) — not the JPA entity.
- There is at least one record used as request/response DTO.
- Jakarta Bean Validation annotations (e.g. @NotBlank, @NotNull) appear on the DTO.

FAIL if:
- The JPA entity is returned directly from the endpoints.
- No DTO/record appears; endpoints operate on the entity.
- No pagination parameters on the list endpoint.
- The response is only prose without controller code.
