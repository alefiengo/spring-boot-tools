---
description: Safe evolution of an already applied Flyway migration
max_turns: 10
allowed_tools: [Read, Glob, Grep, Skill]
---

I deployed V7__create_orders.sql last month. Now I need an extra nullable tracking_code column. Should I edit V7, rename it, or run flyway repair? Show the migration approach and explain the effect on an existing production database.
