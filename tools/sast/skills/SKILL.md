---
name: sast-audit
description: Guía experta para agentes LLM sobre auditoría, análisis de impacto y remediación de vulnerabilidades estáticas de código (SAST) en Java Spring Boot, Ruby, Go, Python MCP, Frontend React y NestJS.
---

# Skill: Remediación SAST Multi-Stack (Semgrep)

## 1. Principio Fundamental
> **Los hallazgos de severidad ALTA o CRÍTICA bloquean la certificación.**
> El agente debe reescribir el patrón vulnerable aplicando código seguro por diseño (*secure by default*) sin alterar la lógica de negocio ni degradar el rendimiento.

---

## 2. Guía de Remediación por Tecnología

### 2.1 Java Spring Boot
* **Inyección SQL en JPA / Hibernate (`java-spring-jpa-sqli`):**
  - *Problema:* Concatenación de cadenas en `entityManager.createQuery("SELECT u FROM User u WHERE u.email = '" + email + "'")`.
  - *Remediación:* Usar parámetros tipados con nombre (`:email`):
    ```java
    TypedQuery<User> query = em.createQuery(
        "SELECT u FROM User u WHERE u.email = :email", User.class);
    query.setParameter("email", email);
    ```
* **CORS Inseguro (`java-spring-cors-wildcard`):**
  - *Remediación:* Especificar dominios permitidos explícitos en lugar de `*`:
    ```java
    @CrossOrigin(origins = {"https://app.miempresa.com"}, allowCredentials = "true")
    ```

---

### 2.2 Ruby / Rails
* **Inyección SQL en ActiveRecord (`ruby-activerecord-sqli`):**
  - *Problema:* `User.where("name = '#{params[:name]}'")` o interpolación en `order`.
  - *Remediación:* Usar argumentos posicionales o hash:
    ```ruby
    # Seguro:
    User.where(name: params[:name])
    User.where("name = ?", params[:name])
    ```
* **Inyección de Comandos (`ruby-command-injection`):**
  - *Problema:* `system("convert #{params[:file]} output.png")`.
  - *Remediación:* Pasar array de argumentos sin invocar la shell:
    ```ruby
    system("convert", params[:file].to_s, "output.png")
    ```

---

### 2.3 Golang
* **Inyección SQL (`go-sql-injection-sprintf`):**
  - *Problema:* `db.Query(fmt.Sprintf("SELECT * FROM users WHERE id = %s", id))`.
  - *Remediación:* Usar placeholders de PostgreSQL (`$1`), MySQL (`?`) u Oracle:
    ```go
    rows, err := db.Query("SELECT id, name FROM users WHERE id = $1", id)
    ```
* **Comandos de Sistema (`go-command-injection-shell`):**
  - *Problema:* `exec.Command("sh", "-c", userInput)`.
  - *Remediación:* Pasar el binario exacto con lista de argumentos:
    ```go
    cmd := exec.Command("git", "log", "-n", "10")
    ```

---

### 2.4 Python MCP (Model Context Protocol) & FastMCP
* **Ejecución Arbitraria en Tools MCP (`python-mcp-unsafe-tool-exec`):**
  - *Problema:* Herramientas MCP que ejecutan comandos con `subprocess.run(user_cmd, shell=True)`.
  - *Remediación:* Validar entradas contra listas blancas o ejecutar con lista de argumentos fijos y `shell=False`:
    ```python
    # Antes (Vulnerable):
    subprocess.run(f"tool --arg {user_input}", shell=True)

    # Después (Seguro):
    subprocess.run(["tool", "--arg", shlex.quote(user_input)], shell=False, check=True)
    ```
* **Validación de Parámetros en Tools MCP:**
  - Emplea modelos Pydantic con validadores (`Field(..., regex=...)`, `Field(max_length=...)`) para garantizar que el modelo no inyecte payloads maliciosos.

---

### 2.4.1 Python general (reglas `python-*`)
| Regla | Remediación |
| --- | --- |
| `python-subprocess-shell-true` | Pasar la orden como lista (`subprocess.run(["git", "status"], check=True)`), sin `shell=True` |
| `python-unsafe-deserialization` | `json` o `yaml.safe_load`; nunca `pickle` con datos externos |
| `python-tls-verification-disabled` | Quitar `verify=False`; usar `verify="/ruta/ca.pem"` si la CA es interna |
| `python-jwt-signature-not-verified` | `jwt.decode(token, clave, algorithms=["HS256"])` con la firma verificada |
| `python-debug-mode-enabled` | Leer el modo debug de una variable de entorno, apagado por defecto |
| `python-weak-hash` | `hashlib.sha256`; contraseñas con `bcrypt`/`argon2`; si no es seguridad, `usedforsecurity=False` |
| `python-insecure-tempfile` | `tempfile.NamedTemporaryFile()` o `tempfile.mkstemp()` |
| `python-hardcoded-secret-key` | `os.environ["SECRET_KEY"]` o un gestor de secretos |

### 2.5 Frontend React
* **Inyección XSS con `dangerouslySetInnerHTML` (`react-dangerouslysetinnerhtml`):**
  - *Problema:* Renderizado de HTML crudo sin sanitización.
  - *Remediación:* Sanitizar con `dompurify` antes de renderizar:
    ```typescript
    import DOMPurify from 'dompurify';

    function SafeHtml({ content }: { content: string }) {
      const clean = DOMPurify.sanitize(content);
      return <div dangerouslySetInnerHTML={{ __html: clean }} />;
    }
    ```
* **Fuga de Secretos en Build del Cliente (`react-hardcoded-secret`):**
  - *Regla de Oro:* NUNCA coloques llaves secretas de Stripe, AWS o tokens JWT de backend en código React. Esas operaciones deben residir en el backend.

---

### 2.6 NestJS Node.js TypeScript
* **Validación Global de DTOs:**
  - Asegúrate de activar el pipe global de validación en `main.ts`:
    ```typescript
    app.useGlobalPipes(
      new ValidationPipe({
        whitelist: true,               // Elimina propiedades no declaradas en el DTO
        forbidNonWhitelisted: true,    // Lanza error si envían propiedades adicionales
        transform: true,               // Transforma tipos automáticamente
      }),
    );
    ```
* **Inyección SQL en TypeORM / Prisma (`nestjs-raw-sql-query`):**
  - Evita interpolar cadenas en `$queryRawUnsafe` o `query()`. Usa `$queryRaw` con tagged template literals (que parametriza de forma nativa) o QueryBuilder con `.setParameter()`.
