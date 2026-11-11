---
description: Consistent error contract that preserves existing clients
max_turns: 10
allowed_tools: [Read, Glob, Grep, Skill]
---

Our Spring Boot 4 REST API returns a different error JSON shape from each controller. Existing mobile clients read a top-level "message" field from 404 responses. Design one consistent error contract for 400 validation errors and 404 not found, and explain how existing clients keep working during the change.
