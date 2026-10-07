---
type: llm
---

PASS only if it uses real org.springframework.modulith dependencies/BOM, @ApplicationModule(allowedDependencies=...) in package-info.java, and ApplicationModules.of(Application.class).verify() in a test. It must explain that package boundary violations are checked by verification rather than claiming the Java compiler automatically enforces them. FAIL for spring-boot-starter-modulith, module.config, or an unexplained injected ApplicationModules bean.
