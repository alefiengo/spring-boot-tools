---
description: Diagnose a module compile failure without a full clean rebuild
max_turns: 10
allowed_tools: [Read, Glob, Grep, Skill]
---

Our Spring Boot multi-module Maven build fails only in the orders-api module with "cannot find symbol: class OrderMapperImpl" after I added MapStruct 1.6.3 as a dependency. The other modules compile. How should I diagnose this and rebuild only what is needed? Don't run anything; explain the steps and the likely fix.
