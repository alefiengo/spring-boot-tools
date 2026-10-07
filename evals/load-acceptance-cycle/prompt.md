---
description: Full load acceptance and interpretation
max_turns: 10
allowed_tools: [Read, Glob, Grep, Skill]
---

Design an opt-in local load test for our user API. We need a 20 arrivals/second workload, p95 below 500ms and less than 1% errors. Explain run commands, data seed, what artifacts to retain and what to do if request count is zero or k6 drops iterations.
