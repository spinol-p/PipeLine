---
name: contracts-audit
description: Guía experta para agentes LLM sobre gobernanza y diseño de contratos OpenAPI en Spring Boot, NestJS, Python FastAPI/MCP y Frontend React.
---

# Skill: Gobernanza de Contratos API Multi-Stack (Spectral)

## 1. Principio Fundamental
> **El contrato OpenAPI es la fuente única de verdad.**
> Los microservicios deben exponer su documentación OpenAPI 3.0/3.1 generada de forma fiel al código y sin romper compatibilidad hacia atrás.

---

## 2. Generación y Buenas Prácticas por Stack

### 2.1 Java Spring Boot (SpringDoc OpenAPI)
* **Dependencia:** `org.springdoc:springdoc-openapi-starter-webmvc-ui:2.x`.
* **Anotaciones Clave:**
  - `@Tag(name = "Usuarios", description = "Operaciones de gestión de usuarios")` en el `@RestController`.
  - `@Operation(summary = "Obtener perfil", description = "Devuelve el perfil por ID")` en cada método.
  - `@ApiResponse(responseCode = "200", description = "Usuario encontrado", content = @Content(schema = @Schema(implementation = UserDTO.class)))`.
  - `@ApiResponse(responseCode = "404", description = "Usuario no encontrado")`.

### 2.2 NestJS Node.js TypeScript (`@nestjs/swagger`)
* **Dependencia:** `@nestjs/swagger`.
* **Configuración en `main.ts`:**
  ```typescript
  const config = new DocumentBuilder()
    .setTitle('API de Clientes')
    .setDescription('Servicio de gestión de clientes y compras')
    .setVersion('1.0')
    .addBearerAuth()
    .build();
  const document = SwaggerModule.createDocument(app, config);
  SwaggerModule.setup('api/docs', app, document);
  ```
* **Decoradores en DTOs y Controladores:**
  - En DTOs: `@ApiProperty({ description: 'Correo del cliente', example: 'usuario@empresa.com' })`.
  - En Controladores: `@ApiOperation({ summary: 'Crea un nuevo usuario' })`, `@ApiCreatedResponse(...)`.

### 2.3 Python MCP / FastAPI
* **FastAPI:** Genera OpenAPI automáticamente a partir de los modelos Pydantic y anotaciones de tipo (`Annotated`, `Query`, `Path`).
* **Documentación de Tools MCP:**
  - En servidores MCP, cada `@mcp.tool()` debe contar con un **docstring** completo y conciso. El LLM utiliza el docstring y los esquemas de tipos para decidir qué herramienta invocar y cómo formular los argumentos.

### 2.4 Frontend React (Consumo Tipado de Contratos)
* **Generación de Clientes:**
  - No escribas tipos de API manualmente en React.
  - Genera tipos TypeScript de forma automatizada a partir del `openapi.yaml` del backend usando herramientas como `openapi-typescript` o `orval`:
    ```bash
    npx openapi-typescript contracts/openapi.yaml -o src/api/schema.d.ts
    ```
  - Esto garantiza que cualquier cambio de contrato en el backend se detecte como un error de compilación (`tsc --noEmit`) en el frontend inmediatamente.
