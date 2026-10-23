from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional
from mcp.server.mcpserver import MCPServer

from core.base_tool import BaseTool
from core.models import Finding, ProjectConfig, Severity, StageResult, StageStatus
from core.runner import DockerRunner
from core.versions import get_docker_image


class DockerfileTool(BaseTool):
    """Herramienta de análisis estático de buenas prácticas y seguridad en Dockerfile con Hadolint."""

    @property
    def name(self) -> str:
        return "dockerfile"

    @property
    def stage_name(self) -> str:
        return "dockerfile"

    @property
    def description(self) -> str:
        return "Analiza el Dockerfile del servicio buscando malas prácticas, riesgos de seguridad y capas no optimizadas."

    @property
    def image(self) -> str:
        return get_docker_image("IMG_HADOLINT", "hadolint/hadolint:v2.12.0-alpine")

    def run(
        self,
        project_path: str,
        run_dir: str,
        config: Optional[ProjectConfig] = None,
    ) -> StageResult:
        stage_out = Path(run_dir) / self.stage_name
        stage_out.mkdir(parents=True, exist_ok=True)
        log_file = stage_out / "stage.log"
        dockerfile_path = Path(project_path) / "Dockerfile"

        # Si el proyecto no tiene Dockerfile, se omite limpiamente
        if not dockerfile_path.exists():
            return StageResult(
                stage=self.stage_name,
                status=StageStatus.SKIPPED,
                tool="hadolint",
                error_message="No se encontró archivo Dockerfile en la raíz del proyecto",
            )

        container_cmd = ["hadolint", "-f", "json", "/src/Dockerfile"]

        volumes = {
            project_path: "/src:ro",
            str(stage_out): "/out:rw",
        }

        res = DockerRunner.run_container(
            image=self.image,
            command=container_cmd,
            volumes=volumes,
            timeout_seconds=180,
        )

        log_file.write_text(res.output, encoding="utf-8")

        findings: List[Finding] = []
        parse_error = None

        if res.exit_code in (0, 1):
            raw_json = res.stdout.strip()
            if not raw_json and res.exit_code == 0:
                findings = []
            elif raw_json.startswith("["):
                try:
                    findings = self._parse_json(raw_json)
                except Exception as ex:
                    parse_error = f"Error al procesar JSON de Hadolint: {ex}"
            else:
                parse_error = f"Hadolint terminó en código {res.exit_code} pero la salida no fue JSON válido: {res.output[:200]}"

        if parse_error:
            status = StageStatus.ERROR
            err_msg = parse_error
        elif res.exit_code == 0:
            status = StageStatus.PASS
            err_msg = None
        elif res.exit_code == 1:
            has_errors = any(f.severity in (Severity.CRITICAL, Severity.HIGH) for f in findings)
            if has_errors:
                status = StageStatus.FAIL
                err_msg = "Se encontraron infracciones de nivel ERROR en el Dockerfile"
            else:
                status = StageStatus.PASS
                err_msg = None
        else:
            status = StageStatus.ERROR
            err_msg = f"Hadolint falló con código {res.exit_code}: {res.output[:300]}"

        result = StageResult(
            stage=self.stage_name,
            status=status,
            tool="hadolint",
            tool_version="2.12.0",
            exit_code=res.exit_code,
            duration_s=round(res.duration_s, 2),
            findings=findings,
            reports=[str(log_file)],
            error_message=err_msg,
            log_file=str(log_file),
            metadata={"rule_violations": len(findings)},
        )

        (stage_out / "result.json").write_text(
            json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        return result

    def _parse_json(self, json_text: str) -> List[Finding]:
        items = json.loads(json_text)
        if not isinstance(items, list):
            raise ValueError("El JSON de Hadolint debe ser una lista")
        findings = []
        for idx, item in enumerate(items):
            code = item.get("code", "DL-UNKNOWN")
            message = item.get("message", "Regla de Dockerfile infringida")
            level = item.get("level", "warning").lower()
            line = item.get("line")

            if level == "error":
                severity = Severity.HIGH
            elif level == "warning":
                severity = Severity.MEDIUM
            else:
                severity = Severity.LOW

            stable_id = f"dockerfile:{code}:Dockerfile:{line or idx}"
            findings.append(
                Finding(
                    id=stable_id,
                    stage=self.stage_name,
                    severity=severity,
                    rule=code,
                    file="Dockerfile",
                    line=line,
                    message=message,
                    fix=f"Consulte la regla Hadolint {code} para aplicar la recomendación estándar.",
                )
            )
        return findings

    def get_mcp_server(self) -> MCPServer:
        server = MCPServer("mcp-dockerfile")

        @server.tool()
        def dockerfile_scan(project_path: str, output_dir: Optional[str] = None) -> str:
            """Ejecuta una auditoría de buenas prácticas y seguridad sobre el Dockerfile."""
            out = output_dir or str(Path(project_path) / ".review" / "dockerfile")
            result = self.run(project_path, out)
            return json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False)

        @server.tool()
        def dockerfile_get_skill() -> str:
            """Obtiene la guía operativa (Skill) del agente para corregir y optimizar Dockerfiles."""
            return self.get_skill()

        return server
