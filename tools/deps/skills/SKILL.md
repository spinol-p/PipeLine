---
name: deps-audit
description: Guía experta para agentes LLM sobre remediación de vulnerabilidades de dependencias (CVE), análisis de licencias y SBOM con Trivy.
---

# Skill: Auditoría de Dependencias y Licencias (Trivy)

## 1. Principio Fundamental
> **Las vulnerabilidades de severidad ALTA o CRÍTICA que cuenten con versión corregida (*fixable*) bloquean la certificación.**
> También bloquean las licencias no permitidas por la política de la organización (por defecto `AGPL-3.0`, `SSPL-1.0`).

## 2. Estrategia de Remediación de CVEs

### 2.1 Dependencias Directas
1. Localiza el archivo de manifiesto (`package.json`, `requirements.txt`, `go.mod`, `pom.xml`).
2. Identifica la versión actual del paquete y la versión mínima corregida recomendada en el reporte.
3. Actualiza el manifiesto a la versión segura:
   - Node: `npm update <paquete>` o editar `package.json` y ejecutar `npm install`.
   - Python: Actualizar en `requirements.txt` o `pyproject.toml`.
   - Go: `go get <paquete>@vX.Y.Z`.
4. Verifica que no existan cambios de compatibilidad rotos (*breaking changes*) entre la versión actual y la nueva.

### 2.2 Dependencias Transitivas (Indirectas)
Si la vulnerabilidad se encuentra en una dependencia profunda de otra librería:
* **Node (npm):** Usa la clave `overrides` en `package.json`:
  ```json
  "overrides": {
    "paquete-vulnerable": "^2.1.0"
  }
  ```
* **Python (pip):** Fija la versión mínima en `requirements.txt` (`paquete-vulnerable>=2.1.0`).
* **Go:** Usa la directiva `replace` en `go.mod`.

## 3. Licencias Prohibidas
* Si se detecta una licencia restrictiva o copyleft agresiva (`AGPL`, `SSPL`, etc.) en una aplicación privativa o de microservicio:
  - Evalúa librerías alternativas con licencias permisivas (`MIT`, `Apache-2.0`, `BSD-3-Clause`, `ISC`).
  - No intentes suprimir la regla sin aprobación explícita de arquitectura legal.

## 4. Excepciones para CVEs Sin Corrección Disponible (Waivers)
Si un CVE es de severidad alta pero **aún no tiene versión corregida por el mantenedor** (*unfixable*) o el código no expone la ruta de ejecución vulnerable:
* Documenta la excepción en `waivers.yaml`:
  ```yaml
  - id: "deps:CVE-2024-XXXX:package-lock.json"
    reason: "No existe parche disponible aún en upstream; la función vulnerable no es invocada por el servicio"
    owner: security-team
    expires: 2026-11-30
  ```
