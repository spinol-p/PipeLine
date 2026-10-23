from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Dict, List, Optional
from mcp.server.mcpserver import MCPServer

from core.base_tool import BaseTool
from core.models import Finding, ProjectConfig, Severity, StageResult, StageStatus
from core.runner import DockerRunner
from core.versions import get_docker_image


class SecretsTool(BaseTool):
    """Herramienta de auditoría de secretos y credenciales utilizando Gitleaks."""

    @property
    def name(self) -> str:
        return "secrets"

    @property
    def stage_name(self) -> str:
        return "secrets"

    @property
    def description(self) -> str:
        return "Detecta credenciales, API keys y secretos expuestos en código e historial git."

    @property
    def image(self) -> str:
        return get_docker_image("IMG_GITLEAKS", "zricethezav/gitleaks:v8.21.2")

    def run(
        self,
        project_path: str,
        run_dir: str,
        config: Optional[ProjectConfig] = None,
    ) -> StageResult:
        stage_out = Path(run_dir) / self.stage_name
        stage_out.mkdir(parents=True, exist_ok=True)
        report_sarif = stage_out / "report.sarif"
        log_file = stage_out / "stage.log"

        # Comprobar si hay repositorio .git en el proyecto
        is_git_repo = (Path(project_path) / ".git").exists()
        scan_mode = "git" if is_git_repo else "dir"

        container_cmd = [
            scan_mode,
            "/src",
            "--redact",
            "--no-banner",
            "--report-format",
            "sarif",
            "--report-path",
            "/out/report.sarif",
        ]

        volumes = {
            project_path: "/src:ro",
            str(stage_out): "/out:rw",
        }

        res = DockerRunner.run_container(
            image=self.image,
            command=container_cmd,
            volumes=volumes,
            timeout_seconds=300,
        )

        # Guardar log
        log_file.write_text(res.output, encoding="utf-8")

        # Gitleaks devuelve:
        # 0 = sin secretos
        # 1 = secretos encontrados
        # otro = error
        findings: List[Finding] = []
        reports: List[str] = []

        parse_error = self.validate_execution(
            exit_code=res.exit_code,
            expected_exit_codes=(0, 1),
            report_file=report_sarif,
            tool_name="Gitleaks",
            output_snippet=res.output,
        )

        if not parse_error:
            reports.append(str(report_sarif))
            try:
                findings = self._parse_sarif(report_sarif)
            except Exception as ex:
                parse_error = f"Error al procesar SARIF de Gitleaks: {ex}"

        if parse_error:
            status = StageStatus.ERROR
            err_msg = parse_error
        elif res.exit_code == 1 or len(findings) > 0:
            status = StageStatus.FAIL
            err_msg = f"Se detectaron {len(findings)} secreto(s) en el código"
        else:
            status = StageStatus.PASS
            err_msg = None

        result = StageResult(
            stage=self.stage_name,
            status=status,
            tool="gitleaks",
            tool_version="8.21.2",
            exit_code=res.exit_code,
            duration_s=round(res.duration_s, 2),
            findings=findings,
            reports=reports,
            error_message=err_msg,
            log_file=str(log_file),
            metadata={
                "secret_count": len(findings),
                "scan_mode": scan_mode,
                "is_git_repo": is_git_repo,
            },
        )

        # Guardar result.json de la etapa
        (stage_out / "result.json").write_text(
            json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        return result

    def _parse_sarif(self, sarif_file: Path) -> List[Finding]:
        findings = []
        data = json.loads(sarif_file.read_text(encoding="utf-8"))
        runs = data.get("runs", [])
        for r in runs:
            results = r.get("results", [])
            for idx, res in enumerate(results):
                rule_id = res.get("ruleId", "generic-secret")
                msg = res.get("message", {}).get("text", "Secreto detectado en el código")
                locations = res.get("locations", [])
                file_path = None
                line_num = None
                if locations:
                    phys = locations[0].get("physicalLocation", {})
                    file_path = phys.get("artifactLocation", {}).get("uri")
                    line_num = phys.get("region", {}).get("startLine")

                if file_path:
                    # D23: Quitar prefijos de contenedor (/src/, src/, file://)
                    file_path = re.sub(r"^(?:file://)?(?:/src/|src/|/)", "", file_path)

                # ID estable
                stable_id = f"secrets:{rule_id}:{file_path or 'unknown'}:{line_num or idx}"
                findings.append(
                    Finding(
                        id=stable_id,
                        stage=self.stage_name,
                        severity=Severity.CRITICAL,  # Los secretos siempre son críticos
                        rule=rule_id,
                        file=file_path,
                        line=line_num,
                        message=msg,
                        fix="Revocar la credencial inmediatamente, rotarla y extraerla a variables de entorno.",
                    )
                )
        return findings

    def get_mcp_server(self) -> MCPServer:
        server = MCPServer("mcp-secrets")

        @server.tool()
        def secrets_scan(project_path: str, output_dir: Optional[str] = None) -> str:
            """Ejecuta una auditoría de secretos usando Gitleaks en el proyecto indicado."""
            out = output_dir or str(Path(project_path) / ".review" / "secrets")
            result = self.run(project_path, out)
            return json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False)

        @server.tool()
        def secrets_get_skill() -> str:
            """Obtiene la guía operativa (Skill) del agente para investigar y remediar secretos."""
            return self.get_skill()

        return server
