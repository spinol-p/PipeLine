---
name: test-audit
description: Guía experta para agentes LLM sobre ejecución de pruebas unitarias, diagnóstico de fallos y optimización de cobertura en Java Spring Boot, Ruby, Go, Python MCP, Frontend React y NestJS.
---

# Skill: Pruebas Unitarias y Cobertura Multi-Stack (Test Runner)

## 1. Principio Fundamental
> **0 pruebas o aserciones fallidas bloquean la certificación.**
> El código debe alcanzar el umbral de cobertura configurado (por defecto **60%**).
> Los tests deben ser deterministas, rápidos y no depender de servicios externos reales en pruebas unitarias.

---

## 2. Playbooks Específicos por Stack

### 2.1 Java Spring Boot
* **Herramientas:** JUnit 5, Mockito, Spring Boot Test, JaCoCo.
* **Comando:** `./mvnw -B test` o `mvn -B test` / `./gradlew test`.
* **Mejores Prácticas:**
  - Evita `@SpringBootTest` para pruebas de lógica pura; levanta todo el contexto y es lento.
  - Usa `@ExtendWith(MockitoExtension.class)` para pruebas unitarias de servicios con `@Mock` e `@InjectMocks`.
  - Usa `@WebMvcTest(MiControlador.class)` con `@MockBean` para probar controladores HTTP y validaciones de DTOs sin levantar la base de datos.
  ```java
  @ExtendWith(MockitoExtension.class)
  class UserServiceTest {
      @Mock private UserRepository userRepository;
      @InjectMocks private UserService userService;

      @Test
      void shouldReturnUserWhenExists() {
          when(userRepository.findById(1L)).thenReturn(Optional.of(new User(1L, "Alice")));
          User result = userService.getUser(1L);
          assertNotNull(result);
          assertEquals("Alice", result.getName());
      }
  }
  ```

---

### 2.2 Ruby / Rails
* **Herramientas:** RSpec, FactoryBot, WebMock, SimpleCov.
* **Comando:** `bundle exec rspec`.
* **Mejores Prácticas:**
  - Organiza los tests siguiendo la convención `describe '#metodo'` y `context 'cuando ocurre X'`.
  - Utiliza `let` y `let!` de forma perezosa y Factories en lugar de fixtures rígidas.
  - Bloquea llamadas HTTP salientes con `WebMock.disable_net_connect!`.
  ```ruby
  RSpec.describe UsersController, type: :controller do
    describe 'GET #show' do
      context 'cuando el usuario existe' do
        let(:user) { create(:user) }
        it 'retorna estado 200 y el json del usuario' do
          get :show, params: { id: user.id }
          expect(response).to have_http_status(:ok)
          expect(JSON.parse(response.body)['email']).to eq(user.email)
        end
      end
    end
  end
  ```

---

### 2.3 Golang
* **Herramientas:** `testing`, `testify/assert`, `httptest`.
* **Comando:** `go test -race -coverprofile=/out/coverage.out ./...`.
* **Mejores Prácticas:**
  - Utiliza **Table-Driven Tests** para evaluar múltiples casos de prueba de forma compacta y legible.
  - Corre siempre con el flag `-race` para detectar condiciones de carrera en goroutines.
  - Usa `httptest.NewRecorder()` para validar handlers HTTP sin abrir sockets reales.
  ```go
  func TestCalculateDiscount(t *testing.T) {
      tests := []struct {
          name     string
          amount   float64
          expected float64
      }{
          {"sin descuento", 50.0, 50.0},
          {"con descuento 10%", 150.0, 135.0},
      }
      for _, tt := range tests {
          t.Run(tt.name, func(t *testing.T) {
              got := CalculateDiscount(tt.amount)
              if got != tt.expected {
                  t.Errorf("CalculateDiscount(%v) = %v; esperado %v", tt.amount, got, tt.expected)
              }
          })
      }
  }
  ```

---

### 2.4 Python (general) y Python MCP / FastMCP / FastAPI
* **Herramientas:** `pytest`, `pytest-cov`, `unittest.mock`; `pytest-asyncio` en servicios MCP o async.
* **Qué hace el pipeline:** instala `requirements.txt`, `requirements-dev.txt`, `requirements-test.txt` y el propio paquete
  (`pip install -e .`) si hay `pyproject.toml`; corre `pytest` con cobertura de ramas y lee `junit.xml` y `coverage.json`.
  Si el servicio no configura cobertura, el pipeline excluye las carpetas de pruebas y `conftest.py` del cálculo.
  La imagen sale de `.python-version` o `requires-python` (cota inferior), o de `IMG_PYTHON_STACK` en `versions.env`.
* **Comando local equivalente:** `pytest -q --cov=. --cov-branch --cov-report=term`.
* **Buenas Prácticas generales:**
  - Una prueba por comportamiento, con nombre que lo describa (`test_rechaza_monto_negativo`).
  - `pytest.fixture` para preparar datos y `tmp_path` para archivos; nada de rutas fijas ni archivos compartidos.
  - `pytest.mark.parametrize` en vez de copiar la misma prueba con otros valores.
  - Mockear en el límite (HTTP, base de datos, reloj) con `unittest.mock.patch` o `respx`/`responses`; no mockear la lógica propia.
  - `pytest.raises(ValueError, match="...")` para probar errores, no `try/except` en la prueba.
  - Pruebas deterministas: fijar semillas y fechas (`freezegun` o inyectar el reloj).
* **Servicios MCP / async:**
* **Mejores Prácticas:**
  - Los servidores MCP declaran herramientas asíncronas (`@mcp.tool() async def ...`). Configura `pytest-asyncio` (`@pytest.mark.asyncio`).
  - Prueba las funciones de las herramientas MCP de forma directa pasando parámetros estructurados y validando respuestas JSON.
  - Mockea llamadas a APIs externas o shells que invoque la herramienta.
  ```python
  import pytest
  from unittest.mock import AsyncMock, patch
  from my_mcp_server import query_database_tool

  @pytest.mark.asyncio
  async def test_query_database_tool_success():
      with patch("my_mcp_server.db_client.fetch", new_callable=AsyncMock) as mock_fetch:
          mock_fetch.return_value = [{"id": 1, "name": "Test"}]
          result = await query_database_tool(user_id=1)
          assert "Test" in result
          mock_fetch.assert_called_once_with(1)
  ```

---

### 2.5 Frontend React (Vite / Next.js)
* **Herramientas:** Vitest / Jest, `@testing-library/react`, `@testing-library/user-event`.
* **Comando:** `npx vitest run --coverage` o `npm test -- --watchAll=false`.
* **Mejores Prácticas:**
  - Prueba el comportamiento desde la perspectiva del usuario (usa queries accesibles: `getByRole`, `getByLabelText`, `getByText`).
  - Evita comprobar detalles de implementación internos o estado directo de componentes (`state`).
  - Usa `userEvent.setup()` y `await userEvent.click(...)` en lugar de `fireEvent`.
  ```typescript
  import { render, screen } from '@testing-library/react';
  import userEvent from '@testing-library/user-event';
  import { LoginForm } from './LoginForm';

  test('muestra error si el email es inválido al enviar', async () => {
    const user = userEvent.setup();
    render(<LoginForm onSubmit={vi.fn()} />);

    await user.type(screen.getByLabelText(/correo/i), 'invalido');
    await user.click(screen.getByRole('button', { name: /ingresar/i }));

    expect(await screen.findByText(/formato de correo inválido/i)).toBeInTheDocument();
  });
  ```

---

### 2.6 NestJS Node.js TypeScript
* **Herramientas:** Jest, `@nestjs/testing`, `supertest`.
* **Comando:** `npm run test:cov` o `npm test`.
* **Mejores Prácticas:**
  - Usa `Test.createTestingModule` para simular el contenedor de inyección de dependencias de NestJS.
  - Reemplaza repositorios o servicios externos usando `.overrideProvider().useValue(...)`.
  - Asegúrate de probar pipes de validación (`ValidationPipe`) y Guards de autenticación.
  ```typescript
  describe('UsersService', () => {
    let service: UsersService;
    let repo: jest.Mocked<Repository<User>>;

    beforeEach(async () => {
      const module: TestingModule = await Test.createTestingModule({
        providers: [
          UsersService,
          {
            provide: getRepositoryToken(User),
            useValue: { findOne: jest.fn() },
          },
        ],
      }).compile();

      service = module.get<UsersService>(UsersService);
      repo = module.get(getRepositoryToken(User));
    });

    it('debe retornar usuario si existe', async () => {
      repo.findOne.mockResolvedValue({ id: 1, name: 'Bob' } as User);
      const user = await service.findOne(1);
      expect(user.name).toBe('Bob');
    });
  });
  ```

---

## 3. Diagnóstico y Remediación de Cobertura
Si la etapa falla con `test:coverage_below_threshold`:
1. Revisa el reporte generado en el log de la etapa (`/out/test/stage.log`).
2. Identifica ramas condicionales (`if/else`, bloques `catch`, validaciones de entrada) que no fueron ejercitadas.
3. Escribe pruebas enfocadas en los casos de borde (valores nulos, listas vacías, excepciones esperadas).
