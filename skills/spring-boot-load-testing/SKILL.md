---
name: spring-boot-load-testing
description: >
  Guides load and performance testing of Spring Boot services: Gatling
  (Java DSL recommended for Spring teams) with realistic simulations,
  injection profiles, and response assertions; k6 as the non-JVM alternative;
  correlating results with actuator metrics (HTTP percentiles, Hikari pool,
  JVM memory) and sizing load profiles from real traffic. Use when asked to
  load test, stress test, or benchmark a Spring Boot service.
---

# Load Testing Spring Boot Services

Guides designing and running load tests against Spring Boot services, and interpreting results with runtime metrics.

## Tool choice

| Tool | When |
|---|---|
| **Gatling** (JVM-native) | Default. Runs on the JVM, integrates with Maven/Gradle builds and JVM CI runners. Recommend the **Java DSL** for Spring teams — no Scala knowledge required. |
| **k6** | When the team has no JVM CI runners or already standardizes on JavaScript tooling. Script in JS, run via `k6` CLI. |

## Gatling setup in a Spring Boot project

- Maven: `gatling-maven-plugin` (Gradle: the Gatling Gradle plugin). Declare compatible Gatling Java DSL dependencies and plugin versions from the official setup; Boot does not manage Gatling.
- Simulations in `src/test/java` for Maven Java DSL, or the source set configured by the Gatling Gradle plugin.
- Run with `./mvnw gatling:test` (Gradle: `./gradlew gatlingRun`). Exclude load tests from the normal build (separate profile or naming convention) so `mvn verify` stays fast.

## Realistic simulation (Java DSL)

```java
import io.gatling.javaapi.core.Simulation;
import io.gatling.javaapi.core.ScenarioBuilder;
import io.gatling.javaapi.http.HttpProtocolBuilder;
import static io.gatling.javaapi.core.CoreDsl.*;
import static io.gatling.javaapi.http.HttpDsl.*;

public class UsersLoadSimulation extends Simulation {
    private final HttpProtocolBuilder protocol = http
        .baseUrl(System.getProperty("baseUrl", "http://localhost:8080"))
        .acceptHeader("application/json");

    private final ScenarioBuilder users = scenario("Browse users")
        .exec(http("GET users").get("/api/v1/users?page=0&size=20")
            .check(status().is(200))
            .check(jsonPath("$.data[0].id").saveAs("userId")))
        .exitHereIfFailed()
        .pause(Integer.getInteger("thinkSeconds", 2))
        .exec(http("GET user detail").get("/api/v1/users/#{userId}")
            .check(status().is(200)))
        .pause(Integer.getInteger("thinkSeconds", 3));

    {
        setUp(users.injectOpen(
            rampUsersPerSec(1).to(Double.parseDouble(System.getProperty("usersPerSecond", "20")))
                .during(Integer.getInteger("rampSeconds", 30)),
            constantUsersPerSec(Double.parseDouble(System.getProperty("usersPerSecond", "20")))
                .during(Integer.getInteger("steadySeconds", 300))))
            .protocols(protocol)
            .assertions(global().responseTime().percentile(95).lt(500),
                        global().failedRequests().percent().lt(1.0));
    }
}
```

Seed at least one user; adapt `$.data[0].id` to the actual response contract. The saved ID supplies the detail request. For a feeder instead, use a seeded CSV and call `.feed(csv("users.csv").circular())` before referencing `#{userId}`.

Core building blocks:

- **Open model:** `rampUsersPerSec` and `constantUsersPerSec` express arrivals independent of server response time. A user can make multiple requests, so arrivals/second differ from requests/second.
- **Closed model:** use `injectClosed(rampConcurrentUsers(1).to(200).during(30), constantConcurrentUsers(200).during(300))` for fixed active-user targets. Do not mix closed steps into `injectOpen`.
- Run spike scenarios separately with `atOnceUsers` rather than injecting the same scenario twice in one setup.
- **Assertions:** use `percentile(95)` for an explicit p95 threshold; `percentile3()` is configurable and is not inherently p95.
- Use think time only when it represents the traffic model.

## Execute, accept and interpret

Write the acceptance criteria before the run: workload/arrival rate, warm-up and measurement duration, minimum successful request count, p95/p99, error budget and resource budget. The example's p95 < 500 ms and errors < 1% are illustrative, not universal SLOs. A zero-request run or failed data check is not a passing benchmark.

```bash
# With the project's compatible Gatling Maven plugin configured:
./mvnw gatling:test -Dgatling.simulationClass=com.example.UsersLoadSimulation \
  -DbaseUrl=http://localhost:8080 -DusersPerSecond=20 -DrampSeconds=30 -DsteadySeconds=300
```

Preserve Gatling reports, workload parameters, application revision, data seed and server metrics. Check achieved arrivals/throughput, failed checks and generator CPU before interpreting latency. Compare runs only with equivalent environment, workload and data; change one hypothesis at a time. Report whether each criterion passed, likely bottleneck with evidence and the next experiment. Stop on agreed error/resource limits; a failed assertion must fail the CI experiment job.

### k6 alternative with the same arrival model

```javascript
import http from 'k6/http';
import { check } from 'k6';
export const options = {
  scenarios: { users: { executor: 'constant-arrival-rate', rate: 20,
    timeUnit: '1s', duration: '1m', preAllocatedVUs: 30, maxVUs: 100 } },
  thresholds: { http_req_failed: ['rate<0.01'], http_req_duration: ['p(95)<500'],
    checks: ['rate>0.99'], dropped_iterations: ['count==0'] },
};
export default function () {
  const base = __ENV.BASE_URL || 'http://localhost:8080';
  const list = http.get(`${base}/api/v1/users?page=0&size=20`);
  if (!check(list, { 'list status': r => r.status === 200 })) return;
  let id;
  try { id = list.json().data[0].id; } catch (_) { id = undefined; }
  if (!check(id, { 'seeded user id': value => value !== undefined && value !== null })) return;
  const detail = http.get(`${base}/api/v1/users/${id}`);
  check(detail, { 'detail status': r => r.status === 200 });
}
```

Save as `users.js`, then `BASE_URL=http://localhost:8080 k6 run users.js`. Size generator VUs from iteration duration; dropped iterations signal insufficient generator capacity or excessively slow iterations, and must be reported. This script assumes the same envelope as Gatling; adapt it to the actual contract.

## What to measure beyond the tool

Gatling tells you *what* regressed; actuator tells you *why*. While the load test runs, sample the actuator metrics endpoint (or scrape with Micrometer/your APM):

| Metric | Why it matters |
|---|---|
| `http.server.requests` (percentile histograms) | Server-side p95/p99 vs. client-side latency; a gap means queueing or network. |
| `hikaricp.connections.active` / `.idle` / `.pending` | Pool exhaustion: `active == max` with rising `pending` and latency means the DB pool is the bottleneck. |
| `jvm.memory.used` vs. max, GC pause metrics | GC-driven latency spikes: sawtooth memory with correlated p95 bumps. |
| `system.cpu.usage`, `process.cpu.usage` | Saturated CPU before latency grows points to inefficient code, not the pool. |

**Correlation rules:**

- p95 rises while `hikaricp.connections.pending` climbs → connection pool exhaustion; check pool max size and query duration.
- p95 rises in periodic bursts with GC pause time rising → GC pressure; check allocation rates and heap sizing.
- Latency flat but error rate rises → inspect status codes, rejection counters and downstream failures before attributing the cause.

## Sizing the load profile

- Start from the **real traffic profile**: peak requests/second from production metrics or Nginx/gateway logs — not from an arbitrary "1000 users".
- Model **think time** from observed inter-request gaps; closed-loop user counts × think time determines achieved RPS.
- Test **steady state** (typical peak sustained) plus a **spike scenario** (e.g., 2–3× peak for 60s) to catch pool/queue limits that steady load hides.

## Rules

- Run load tests against a **local profile** (`local` or a dedicated test profile) with production-like data volume — pagination and query plans behave differently on small datasets.
- Run against isolated environments by default. Shared staging/production needs explicit owner authorization, limits and a stop plan.
- Use a **Testcontainers database seeded with representative data** for reproducibility; drop and reseed per run.
- Keep the load generator on separate hardware/containers from the system under test, or the generator's own CPU skews results.
- Record the environment (CPU, memory, JVM flags, data volume) alongside results; a number without context is not comparable across runs.

## References

- [Gatling Maven execution](https://docs.gatling.io/integrations/build-tools/maven-plugin/)
- [k6 arrival-rate executor](https://grafana.com/docs/k6/latest/using-k6/scenarios/executors/constant-arrival-rate/)
- [Gatling injection models](https://docs.gatling.io/concepts/injection/)
- [Gatling assertions](https://docs.gatling.io/concepts/assertions/)
