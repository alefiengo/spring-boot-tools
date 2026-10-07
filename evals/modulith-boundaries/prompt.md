---
description: Executable Modulith boundary verification
max_turns: 10
allowed_tools: [Read, Glob, Grep, Skill]
---

I am building a Spring Boot 4 modular monolith. The order module may access the public user module API, but must never access user.internal. Show the Maven dependency setup, order package metadata, and a plain JUnit boundary verification test. Do not introduce a module.config file.
