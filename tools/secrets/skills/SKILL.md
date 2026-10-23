---
name: secrets-audit
description: Guía experta para agentes LLM sobre auditoría, remediación y rotación de secretos y credenciales detectados por Gitleaks.
---

# Skill: Auditoría y Remediación de Secretos (Gitleaks)

## 1. Principio Fundamental
> **Los secretos NUNCA pueden ser exceptuados mediante `waivers.yaml`.**
> Si se detecta una credencial, token, llave privada o contraseña en el repositorio, la certificación será **RECHAZADA** hasta que sea remediada.

## 2. Diagnóstico del Hallazgo
Cada hallazgo de Gitleaks incluye:
- `id`: `secrets:<regla>:<archivo>:<línea>`
- `file`: Archivo donde se detectó el patrón.
- `rule`: Tipo de secreto (ej. `aws-access-token`, `private-key`, `generic-api-key`).

### Verificación de Falsos Positivos vs Reales
1. **Inspecciona el archivo** en la línea indicada:
   - ¿Es un valor de prueba mockeado evidente (ej. `"dummy_secret_12345"`, `"changeme"`, `"test-token"`)?
   - ¿Es un secreto real (token con entropía real, hash, llave PEM)?
2. **Si es un Falso Positivo genuino (ej. en tests unitarios):**
   - Agrega el fingerprint del secreto al archivo `.gitleaksignore` en la raíz del proyecto.
   - O reemplaza el valor en el test con marcadores explícitos que Gitleaks no clasifique como secretos (ej. `"TEST_ENV_VAR"`).

## 3. Protocolo de Remediación Obligatorio (Si el Secreto es Real)
1. **Rotación Inmediata:** Asume que el secreto está comprometido. El humano debe invalidar o regenerar la credencial en el proveedor (AWS, GitHub, Stripe, DB, etc.).
2. **Extracción del Código:**
   - Elimina el valor hardcodeado del archivo.
   - Carga la credencial mediante variables de entorno del sistema (`process.env.VAR`, `os.environ.get("VAR")`).
   - Declara la variable de entorno en `.env.example` con valor vacío o de ejemplo.
   - Añade el archivo que contiene credenciales locales (`.env`) al `.gitignore`.
3. **Historial de Git (si ya fue commiteado):**
   - Simplemente borrar el secreto en un commit posterior **no es suficiente**; Gitleaks escanea el historial.
   - Usa `git-filter-repo` o `git rebase -i` para expurgar el commit del historial antes de hacer push.

## 4. Verificación de Certificación
Una vez aplicado el cambio:
1. Vuelve a ejecutar la herramienta con `secrets_scan` o a través del orquestador.
2. Comprueba que el estado de la etapa sea `pass` (0 hallazgos).
