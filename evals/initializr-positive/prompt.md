---
description: Explicit new-project request with guarded Initializr capability
max_turns: 10
allowed_tools: [Read, Glob, Grep, Skill]
---

Create a new Spring Boot project in ./generated-orders, a currently absent child
of this evaluation's application workspace. Use group com.example, artifact
orders, Java, and Maven with a web dependency. Inspect the actual cwd and ancestor
project context first. Use official Initializr metadata for unspecified versions
and dependency identifiers. Do not build, start services, publish or push.

Use the plugin's guarded initialize_project capability. If the capability is
unavailable, the context is already a project, or the service fails, report that
generation did not succeed. Do not invent a successful tool result or substitute
a download/unzip or hand-written project. Report returned parameters, target and
files only after actual successful generation.
