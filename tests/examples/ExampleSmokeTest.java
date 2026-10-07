package com.example.fixture;

import java.io.IOException;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicReference;
import javax.crypto.spec.SecretKeySpec;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.slf4j.MDC;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;
import org.springframework.security.oauth2.jose.jws.MacAlgorithm;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.security.oauth2.jwt.JwtEncoder;
import org.springframework.security.oauth2.jwt.NimbusJwtDecoder;
import org.springframework.security.oauth2.jwt.NimbusJwtEncoder;
import com.nimbusds.jose.jwk.source.ImmutableSecret;
import static org.junit.jupiter.api.Assertions.*;

class ExampleSmokeTest {
    @AfterEach
    void cleanMdc() { MDC.clear(); }

    @Test
    void accessTokenCanBeDecodedWithItsSubjectIssuerAndExpiry() {
        var secret = new SecretKeySpec("01234567890123456789012345678901".getBytes(java.nio.charset.StandardCharsets.UTF_8), "HmacSHA256");
        // AccessTokens delegates signing-algorithm configuration to the configured encoder.
        JwtEncoder delegate = new NimbusJwtEncoder(new ImmutableSecret<>(secret));
        JwtEncoder encoder = parameters -> delegate.encode(
            org.springframework.security.oauth2.jwt.JwtEncoderParameters.from(
                org.springframework.security.oauth2.jwt.JwsHeader.with(MacAlgorithm.HS256).build(), parameters.getClaims()));
        JwtDecoder decoder = NimbusJwtDecoder.withSecretKey(secret).macAlgorithm(MacAlgorithm.HS256).build();
        String token = new AccessTokens(encoder, "https://issuer.example").issue("42");
        var jwt = decoder.decode(token);
        assertEquals("42", jwt.getSubject());
        assertEquals("https://issuer.example", jwt.getIssuer().toString());
        assertEquals("users.read", jwt.getClaimAsString("scope"));
        assertEquals(900, java.time.Duration.between(jwt.getIssuedAt(), jwt.getExpiresAt()).toSeconds());
    }

    @Test
    void validationErrorsAreExtensionsWhileDetailStaysText() {
        var exception = ApiErrors.invalidEmail("trace-42");
        assertEquals(400, exception.getStatusCode().value());
        assertEquals("Request validation failed", exception.getBody().getDetail());
        assertEquals("trace-42", exception.getBody().getProperties().get("traceId"));
        assertEquals(List.of(Map.of("field", "email", "message", "must be a valid email")), exception.getBody().getProperties().get("errors"));
    }

    @Test
    void correlationIdIsAvailableDuringRequestAndPreviousMdcIsRestored() throws Exception {
        MDC.put("correlationId", "outer");
        var request = new MockHttpServletRequest();
        request.addHeader("X-Correlation-Id", "request-42");
        var response = new MockHttpServletResponse();
        var observed = new AtomicReference<String>();
        new CorrelationIdFilter().doFilter(request, response, (req, res) -> observed.set(MDC.get("correlationId")));
        assertEquals("request-42", observed.get());
        assertEquals("request-42", response.getHeader("X-Correlation-Id"));
        assertEquals("outer", MDC.get("correlationId"));
    }

    @Test
    void invalidIncomingIdIsReplacedAndMdcIsClearedOnFailure() {
        var request = new MockHttpServletRequest();
        request.addHeader("X-Correlation-Id", "bad\r\nheader");
        var response = new MockHttpServletResponse();
        assertThrows(IOException.class, () -> new CorrelationIdFilter().doFilter(request, response,
            (req, res) -> { throw new IOException("request failed"); }));
        assertDoesNotThrow(() -> java.util.UUID.fromString(response.getHeader("X-Correlation-Id")));
        assertNull(MDC.get("correlationId"));
    }
}
