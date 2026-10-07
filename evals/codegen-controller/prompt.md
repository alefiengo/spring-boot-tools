---
description: A natural request that should trigger the code generation skill
max_turns: 10
allowed_tools: [Read, Glob, Grep, Skill, Agent]
---

I have a Spring Boot 4.1 project with this entity already:

```java
@Entity
@Table(name = "products")
public class Product {
    @Id @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    private String name;
    private String sku;
    private BigDecimal price;
}
```

Create a REST controller for products under /api/v1/products with list
(paginated), get by id, and create endpoints. Follow good practices.
