---
description: A plain JUnit request without Spring that the plugin should NOT hijack
max_turns: 6
allowed_tools: [Skill]
---

Write a JUnit 5 test for this plain Java class. There is no Spring or Spring Boot in this project.

public final class Slug { public static String of(String s) { return s.trim().toLowerCase().replaceAll("\\s+", "-"); } }
