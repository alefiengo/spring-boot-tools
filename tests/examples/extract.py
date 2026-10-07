#!/usr/bin/env python3
"""Compile current fenced examples, so docs cannot drift from validation."""
import re
import shutil
import sys
from pathlib import Path

repo, output = map(Path, sys.argv[1:])
examples = {
    'spring-boot-security-hardening': ['SecurityConfig', 'PasswordHashing', 'ApiRateLimit', 'AccessTokens'],
    'spring-boot-observability': ['DatabaseHealthIndicator', 'CorrelationIdFilter'],
    'spring-boot-api-contracts': ['ApiErrors', 'ApiVersionConfig'],
    'spring-boot-load-testing': ['UsersLoadSimulation'],
    'spring-boot-contract-testing': ['UserClientPactTest', 'UserServiceProviderTest'],
    'spring-boot-modulith': ['ArchitectureTest'],
}
test_classes = {'UsersLoadSimulation', 'UserClientPactTest', 'UserServiceProviderTest', 'ArchitectureTest'}
for skill, classes in examples.items():
    document = (repo / 'skills' / skill / 'SKILL.md').read_text()
    fences = re.findall(r'^```java\n(.*?)^```\s*$', document, re.M | re.S)
    for class_name in classes:
        matches = [code for code in fences if re.search(r'\bclass\s+' + class_name + r'\b', code)]
        if len(matches) != 1:
            raise SystemExit(f'{skill}: expected one {class_name} fence, found {len(matches)}')
        code = matches[0]
        if '...' in code:
            raise SystemExit(f'{skill}: unfinished {class_name} example')
        source_set = 'test' if class_name in test_classes else 'main'
        target = output / f'src/{source_set}/java/com/example/fixture/{class_name}.java'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('package com.example.fixture;\n\n' + code)

# Project-owned inputs referenced by the architecture example.
main = output / 'src/main/java/com/example/fixture'
(main / 'MyApp.java').write_text('''package com.example.fixture;
@org.springframework.boot.autoconfigure.SpringBootApplication
public class MyApp {}
''')
(main / 'orders').mkdir()
(main / 'orders/Order.java').write_text('package com.example.fixture.orders; public record Order(long id) {}\n')
test = output / 'src/test/java/com/example/fixture'
shutil.copyfile(repo / 'tests/examples/ExampleSmokeTest.java', test / 'ExampleSmokeTest.java')
print('Extracted 12 Java examples from current skills; compilation and behavioral checks follow.')
