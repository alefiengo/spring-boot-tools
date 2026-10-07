---
description: Actual Boot 4 OTLP and servlet filter APIs
max_turns: 10
allowed_tools: [Read, Glob, Grep, Skill]
---

I use Spring Boot 4.1 with Spring MVC. Show the dependency and application.yml to export traces over OTLP HTTP to localhost:4318/v1/traces, and a servlet filter that sets X-Correlation-Id on the response. The code must use the actual HTTP servlet request/response APIs and compile with appropriate imports.
