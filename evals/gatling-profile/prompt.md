---
description: Runnable open-model load simulation
max_turns: 10
allowed_tools: [Read, Glob, Grep, Skill]
---

For a Spring Boot API returning a JSON array of user ids, show a Gatling Java DSL simulation that sustains 20 new users per second for one minute after ramp-up. Each virtual user fetches the list and then one returned user id. Include checks and a p95 assertion. Explain why this is an open workload model.
