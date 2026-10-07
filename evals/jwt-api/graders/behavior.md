---
type: llm
---

PASS only if the encoder is called with JwtEncoderParameters/JwtClaimsSet, the compact token is obtained with getTokenValue(), and the answer retains or explicitly implements CSRF protection for the cookie-authenticated endpoint. FAIL for an invented JwtToken builder, assigning JwtEncoder.encode directly to String, or disabling CSRF globally just because sessions are stateless.
