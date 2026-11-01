# Working on spring-boot-tools

This repository ships a Claude Code plugin, not a Spring Boot application.
Read `CONTRIBUTING.md` and `docs/architecture.md` before changing executable behavior.
The agents in `agents/` and skills in `skills/` are shipped product components;
do not repurpose them as the team maintaining this repository.

For explicitly requested team coordination, read
`docs/skills/repo-director/SKILL.md`. Use Herdr only when the user requests it
and `HERDR_ENV=1`. Ordinary small edits do not require a team.

The director assigns a bounded task and file ownership before parallel edits.
One writer owns a file at a time. Reviewers report evidence without editing.
Keep the shared Python runtime authoritative for project discovery and build
execution. Do not introduce competing implementations in hooks or MCP.

Follow the validation commands in `CONTRIBUTING.md`, scaled to the change.
Behavioral Claude evaluations make model calls: run only when explicitly
requested. Do not publish, push, merge, or change account configuration merely
because team coordination is authorized.
