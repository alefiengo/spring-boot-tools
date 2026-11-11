---
expect:
  userRequested: true
  targetDirectory: string
---

{"ok": true, "targetDirectory": "{{input.targetDirectory}}", "source": "https://start.spring.io", "parameters": {"type": "maven-project", "language": "java", "groupId": "{{input.groupId}}", "artifactId": "{{input.artifactId}}", "bootVersion": "4.1.0", "javaVersion": "25", "dependencies": "web"}, "files": ["pom.xml", "mvnw", "mvnw.cmd", ".mvn/wrapper/maven-wrapper.properties", "src/main/java/com/example/orders/OrdersApplication.java", "src/main/resources/application.properties", "src/test/java/com/example/orders/OrdersApplicationTests.java"], "buildExecuted": false}
