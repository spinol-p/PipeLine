from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional
from mcp.server.mcpserver import MCPServer

from core.base_tool import BaseTool
from core.models import Finding, ProjectConfig, Severity, StageResult, StageStatus
from core.runner import DockerRunner
from core.versions import get_docker_image


class SastTool(BaseTool):
    """Herramienta de análisis estático de seguridad (SAST) utilizando Semgrep."""

    @property
    def name(self) -> str:
        return "sast"

    @property
    def stage_name(self) -> str:
        return "sast"

    @property
    def description(self) -> str:
        return "Análisis estático de código fuente para detectar patrones de vulnerabilidad y fallos OWASP."

    @property
    def image(self) -> str:
        return get_docker_image("IMG_SEMGREP", "semgrep/semgrep:1.90.0")

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
        rules_dir = Path(__file__).parent / "rules"

        copy_cmd = (
            "mkdir -p /src_clean && "
            "tar -C /src_ro "
            "--exclude='node_modules' "
            "--exclude='vendor' "
            "--exclude='target' "
            "--exclude='dist' "
            "--exclude='coverage' "
            "--exclude='.git' "
            "--exclude='.venv' "
            "--exclude='venv' "
            "--exclude='__pycache__' "
            "--exclude='.tox' "
            "-cf - . | tar -C /src_clean -xf -"
        )
        container_cmd = [
            "/bin/sh",
            "-c",
            f"{copy_cmd} && semgrep scan --config /rules --metrics=off --sarif --output /out/report.sarif /src_clean",
        ]

        volumes = {
            project_path: "/src_ro:ro",
            str(stage_out): "/out:rw",
            str(rules_dir): "/rules:ro",
        }

        res = DockerRunner.run_container(
            image=self.image,
            command=container_cmd,
            volumes=volumes,
            timeout_seconds=300,
        )

        log_file.write_text(res.output, encoding="utf-8")

        findings: List[Finding] = []
        reports: List[str] = []
        stage_metadata: Dict[str, Any] = {}
        parse_error = None

        if res.exit_code in (0, 1):
            if report_sarif.exists():
                reports.append(str(report_sarif))
                try:
                    findings, stage_metadata = self._parse_sarif(report_sarif)
                except Exception as ex:
                    parse_error = f"Error al procesar SARIF de Semgrep: {ex}"
            else:
                parse_error = "Semgrep terminó en código esperado pero no generó el archivo SARIF"
        else:
            parse_error = f"Semgrep finalizó con error no recuperable (código {res.exit_code}): {res.output[:300]}"

        if parse_error:
            status = StageStatus.ERROR
            err_msg = parse_error
        else:
            blocking_count = sum(1 for f in findings if f.severity in (Severity.CRITICAL, Severity.HIGH))
            if blocking_count > 0:
                status = StageStatus.FAIL
                err_msg = f"Se detectaron {len(findings)} hallazgo(s) SAST ({blocking_count} bloqueantes)"
            else:
                status = StageStatus.PASS
                err_msg = None

        result = StageResult(
            stage=self.stage_name,
            status=status,
            tool="semgrep",
            tool_version="1.90.0",
            exit_code=res.exit_code,
            duration_s=round(res.duration_s, 2),
            findings=findings,
            reports=reports,
            error_message=err_msg,
            log_file=str(log_file),
            metadata=stage_metadata,
        )

        (stage_out / "result.json").write_text(
            json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        return result

    def _parse_sarif(self, sarif_file: Path) -> Tuple[List[Finding], Dict[str, Any]]:
        findings = []
        metadata: Dict[str, Any] = {}
        data = json.loads(sarif_file.read_text(encoding="utf-8"))
        runs = data.get("runs", [])
        if not runs:
            return findings, metadata

        run = runs[0]
        tool_driver = run.get("tool", {}).get("driver", {})
        metadata["rules_count"] = len(tool_driver.get("rules", []))

        invocations = run.get("invocations", [])
        if invocations:
            notifications = invocations[0].get("toolExecutionNotifications", [])
            if notifications:
                metadata["notifications_count"] = len(notifications)
                metadata["partially_analyzed_files"] = [
                    n.get("message", {}).get("text", "") for n in notifications[:5]
                ]

        results = run.get("results", [])
        for idx, item in enumerate(results):
            rule_id = item.get("ruleId", "sast-rule")
            msg = item.get("message", {}).get("text", "Vulnerabilidad detectada")
            level = item.get("level", "warning").lower()

            # Severidad nativa (D8)
            props = item.get("properties", {})
            sec_severity = props.get("security-severity")
            if sec_severity is not None:
                try:
                    score = float(sec_severity)
                    if score >= 9.0:
                        severity = Severity.CRITICAL
                    elif score >= 7.0:
                        severity = Severity.HIGH
                    elif score >= 4.0:
                        severity = Severity.MEDIUM
                    else:
                        severity = Severity.LOW
                except Exception:
                    severity = Severity.HIGH if level == "error" else Severity.MEDIUM
            else:
                if level in ("error", "critical"):
                    severity = Severity.HIGH
                elif level == "warning":
                    severity = Severity.MEDIUM
                else:
                    severity = Severity.LOW

            locations = item.get("locations", [])
            file_path = None
            line_num = None
            if locations:
                phys = locations[0].get("physicalLocation", {})
                file_path = phys.get("artifactLocation", {}).get("uri")
                if file_path:
                    file_path = file_path.replace("/src_clean/", "").replace("src_clean/", "")
                line_num = phys.get("region", {}).get("startLine")

            stable_id = f"sast:{rule_id}:{file_path or 'unknown'}:{line_num or idx}"
            findings.append(
                Finding(
                    id=stable_id,
                    stage=self.stage_name,
                    severity=severity,
                    rule=rule_id,
                    file=file_path,
                    line=line_num,
                    message=msg,
                    fix="Revisar el patrón de código, aplicar consultas parametrizadas o validación estricta de entradas.",
                )
            )

        metadata["findings_count"] = len(findings)
        return findings, metadata

    def get_mcp_server(self) -> MCPServer:
        server = MCPServer("mcp-sast")

        @server.tool()
        def sast_scan(project_path: str, output_dir: Optional[str] = None) -> str:
            """Ejecuta un escaneo SAST con Semgrep en el código fuente."""
            out = output_dir or str(Path(project_path) / ".review" / "sast")
            result = self.run(project_path, out)
            return json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False)

        @server.tool()
        def sast_get_skill() -> str:
            """Obtiene la guía operativa (Skill) del agente para auditar y remediar vulnerabilidades SAST."""
            return self.get_skill()

        return server
