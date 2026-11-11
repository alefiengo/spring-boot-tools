---
description: CI separates verification from image publication
max_turns: 10
allowed_tools: [Read, Glob, Grep, Skill]
---

Write a GitHub Actions workflow for our Spring Boot Maven service: verify every pull request with unit and Testcontainers integration tests, and publish a container image to GHCR only after verification passes on main. Explain the permissions you chose.
