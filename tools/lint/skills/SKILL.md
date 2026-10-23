# Skill: Lint (Linter & Formatting)

## Rol
Herramienta de revisión de estilo, calidad y formato de código para asegurar estándares del lenguaje antes de compilar o probar.

## Comandos por stack
- **Node / NestJS:** `npm run lint` / `eslint`
- **Ruby:** `bundle exec rubocop`
- **Go:** `gofmt` y `go vet`
- **Java:** `spotless:check` o `mvn compile`
- **Python:** `ruff check` + `ruff format --check` (y `mypy` si el servicio lo configura)

## Buenas prácticas
1. Corregir advertencias de estilo automáticamente con el formateador del stack (ej. `rubocop -A`, `npm run lint -- --fix`).
2. Resolver importaciones no utilizadas, variables muertas y errores de tipado reportados por el compilador o linter.

## Python: qué revisa el pipeline

**Configuración.** Si el servicio declara su propia configuración de Ruff (`ruff.toml`, `.ruff.toml` o
`[tool.ruff]` en `pyproject.toml`), se usa esa. Si no, se aplica la base del pipeline
(`tools/lint/rules/python/ruff.toml`). `mypy` corre solo si el servicio lo configura (`mypy.ini`,
`[tool.mypy]` o `[mypy]` en `setup.cfg`): el tipado estricto es una decisión del equipo, no se impone.

**Familias de reglas de la base y por qué importan**

| Familia | Qué detecta | Severidad |
| --- | --- | --- |
| `F` (pyflakes) | Nombres sin definir, imports y variables sin usar | Alta |
| `B` (bugbear) | Argumentos por defecto mutables, `except` mal escritos, bucles con closures | Alta |
| `S` (bandit) | `shell=True`, `pickle`, contraseñas en el código, `assert` fuera de pruebas | Alta |
| `BLE` / `ASYNC` | `except Exception` que se traga errores; llamadas bloqueantes en código `async` | Alta |
| `E`, `W`, `I`, `N` | PEP 8, orden de imports, nombres | Media |
| `UP`, `C4`, `SIM`, `RET`, `PIE` | Sintaxis moderna y código más simple | Media |
| `DTZ`, `G`, `LOG` | Fechas sin zona horaria; logging con f-strings | Media |
| `RUFF_FORMAT` | Archivo que no cumple `ruff format` | Media |

**Cómo corregir rápido**
1. `ruff check --fix .` corrige automáticamente imports, orden y sintaxis moderna.
2. `ruff format .` deja todo el formato consistente (equivalente a Black).
3. Lo que queda requiere criterio: revisar cada `S`, `B` y `BLE` antes de silenciarlo.
4. Para silenciar un caso puntual y justificado: `# noqa: S603  # motivo` en la línea, nunca un `noqa` sin código.

**Buenas prácticas que el lint no cubre del todo**
- Declarar dependencias con versión acotada (`requirements.txt` con `==` o un lock de Poetry/uv).
- Declarar `requires-python` o `.python-version`: el pipeline prueba con esa versión.
- Anotaciones de tipo en las funciones públicas; activar `mypy` cuando el equipo esté listo.
- `logging` en vez de `print` en servicios; nunca registrar secretos ni datos personales.
