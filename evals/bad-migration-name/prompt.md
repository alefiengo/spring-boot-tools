---
description: A bad Flyway migration name that should trigger the database skill conventions
max_turns: 8
allowed_tools: [Read, Glob, Grep, Skill]
---

I just added a migration file to my Spring Boot project called
`add_new_users_table.sql` in src/main/resources/db/migration/. The file creates
a users table with an email column. Is my migration file OK? What naming do you
recommend?
