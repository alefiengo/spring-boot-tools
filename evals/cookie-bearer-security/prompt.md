---
description: Separate browser cookies from bearer API
max_turns: 10
allowed_tools: [Read, Glob, Grep, Skill]
---

Our Spring app uses a browser session cookie plus bearer tokens for machine clients. A POST from the browser returns 403. Should we disable CSRF globally? Show how to diagnose and preserve both authentication flows.
