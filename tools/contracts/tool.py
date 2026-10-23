from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional
import yaml
from mcp.server.mcpserver import MCPServer

from core.base_tool import BaseTool
from core.models import Finding, ProjectConfig, Severity, StageResult, StageStatus
from core.runner import DockerRunner
from core.versions import get_docker_image


class ContractsTool(BaseTool):
    """Herramienta de validación de contratos OpenAPI (Spectral), AsyncAPI y Protobuf (Buf)."""

    @property
    def name(self) -> str:
        return "contracts"

    @property
    def stage_name(self) -> str:
        return "contracts"

    @property
    def description(self) -> str:
        return "Valida la calidad, formato y conformidad de especificaciones OpenAPI, AsyncAPI y Protobuf."

    @property
    def image(self) -> str:
        return get_docker_image("IMG_SPECTRAL", "stoplight/spectral:6.13.1")

    @property
    def buf_image(self) -> str:
        return get_docker_image("IMG_BUF", "bufbuild/buf:1.47.2")

    def run(
        self,
        project_path: str,
        run_dir: str,
        config: Optional[ProjectConfig] = None,
    ) -> StageResult:
        stage_out = Path(run_dir) / self.stage_name
        stage_out.mkdir(parents=True, exist_ok=True)
        log_file = stage_out / "stage.log"
        rules_file = Path(__file__).parent / "rules" / ".spectral.yaml"
        p_path = Path(project_path)

        findings: List[Finding] = []
        reports: List[str] = []
        logs: List[str] = []
        total_duration = 0.0

        # 1. Buscar OpenAPI
        openapi_file = None
        if config and config.openapi_file:
            cand = p_path / config.openapi_file
            if cand.exists():
                openapi_file = config.openapi_file

        if not openapi_file:
            for pattern in ["openapi.yaml", "openapi.json", "swagger.yaml", "swagger.json", "contracts/openapi.yaml"]:
                if (p_path / pattern).exists():
                    openapi_file = pattern
                    break

        if not openapi_file:
            for cand in p_path.glob("contracts/**/*.yaml"):
                if not cand.name.endswith("_events.yaml") and not cand.name.endswith("_events.yml"):
                    openapi_file = str(cand.relative_to(project_path)).replace("\\", "/")
                    break

        # 2. Buscar AsyncAPI
        events_files = []
        for cand in p_path.glob("contracts/**/*_events.y*ml"):
            events_files.append(str(cand.relative_to(project_path)).replace("\\", "/"))

        # 3. Buscar Protobuf (.proto)
        proto_files = []
        for cand in p_path.glob("**/*.proto"):
            cand_str = str(cand.relative_to(project_path)).replace("\\", "/")
            if not any(ign in cand_str for ign in ["vendor/", "node_modules/", "target/", ".git/"]):
                proto_files.append(cand_str)

        if not openapi_file and not events_files and not proto_files:
            return StageResult(
                stage=self.stage_name,
                status=StageStatus.SKIPPED,
                tool="contracts",
                error_message="No se encontraron contratos OpenAPI, AsyncAPI ni Protobuf",
            )

        # Ejecutar Spectral para OpenAPI si existe
        if openapi_file:
            report_json = stage_out / "report.json"
            container_cmd = [
                "lint",
                f"/src/{openapi_file}",
                "--ruleset",
                "/rules/.spectral.yaml",
                "-f",
                "json",
                "-o",
                "/out/report.json",
            ]
            volumes = {
                project_path: "/src:ro",
                str(stage_out): "/out:rw",
                str(rules_file.parent): "/rules:ro",
            }
            res_spectral = DockerRunner.run_container(
                image=self.image,
                command=container_cmd,
                volumes=volumes,
                timeout_seconds=180,
            )
            total_duration += res_spectral.duration_s
            logs.append(f"=== Spectral OpenAPI ({openapi_file}) ===\n{res_spectral.output}")

            parse_error = self.validate_execution(
                exit_code=res_spectral.exit_code,
                expected_exit_codes=(0, 1),
                report_file=report_json,
                tool_name="Spectral",
                output_snippet=res_spectral.output,
            )
            if parse_error:
                return StageResult(
                    stage=self.stage_name,
                    status=StageStatus.ERROR,
                    tool="contracts",
                    error_message=parse_error,
                    log_file=str(log_file),
                )
            reports.append(str(report_json))
            try:
                findings.extend(self._parse_json(report_json, openapi_file))
            except Exception as ex:
                return StageResult(
                    stage=self.stage_name,
                    status=StageStatus.ERROR,
                    tool="contracts",
                    error_message=f"Error procesando JSON de Spectral: {ex}",
                    log_file=str(log_file),
                )

        # Validar AsyncAPI si existe
        for ev_file in events_files:
            ev_path = p_path / ev_file
            try:
                ev_content = yaml.safe_load(ev_path.read_text(encoding="utf-8"))
                if not isinstance(ev_content, dict) or "asyncapi" not in ev_content:
                    findings.append(
                        Finding(
                            id=f"contracts:asyncapi:missing_spec:{ev_file}",
                            stage=self.stage_name,
                            severity=Severity.HIGH,
                            rule="ASYNCAPI_INVALID_SPEC",
                            file=ev_file,
                            message="El archivo de eventos no define un campo válido 'asyncapi'",
                            fix="Especificar la versión de AsyncAPI (ej. 'asyncapi: 3.0.0').",
                        )
                    )
                logs.append(f"=== AsyncAPI ({ev_file}) ===\nValidación de sintaxis conforme.")
            except Exception as ex:
                findings.append(
                    Finding(
                        id=f"contracts:asyncapi:parse_error:{ev_file}",
                        stage=self.stage_name,
                        severity=Severity.HIGH,
                        rule="ASYNCAPI_PARSE_ERROR",
                        file=ev_file,
                        message=f"Error de sintaxis YAML en contrato AsyncAPI: {ex}",
                        fix="Corregir formato YAML del archivo de eventos.",
                    )
                )

        # Validar Protobuf con Buf si existen archivos .proto
        if proto_files:
            proto_dirs = sorted(list({str(Path(pf).parent).replace("\\", "/") for pf in proto_files}))
            for p_dir in proto_dirs:
                buf_res = DockerRunner.run_container(
                    image=self.buf_image,
                    command=["lint", f"/src/{p_dir}", "--error-format=json"],
                    volumes={project_path: "/src:ro"},
                    timeout_seconds=180,
                )
                total_duration += buf_res.duration_s
                logs.append(f"=== Buf Protobuf (/src/{p_dir}) ===\n{buf_res.output}")

                if buf_res.exit_code in (0, 1, 100):
                    for line in buf_res.output.splitlines():
                        line = line.strip()
                        if not line or not line.startswith("{"):
                            continue
                        try:
                            item = json.loads(line)
                            p_file = item.get("path", "")
                            p_file = re.sub(r"^(?:file://)?(?:/src/|src/|/)", "", p_file)
                            p_rule = item.get("type", "BUF_LINT")
                            p_msg = item.get("message", "Violación de linter Buf")
                            p_line = item.get("start_line", 1)
                            findings.append(
                                Finding(
                                    id=f"contracts:buf:{p_rule}:{p_file}:{p_line}",
                                    stage=self.stage_name,
                                    severity=Severity.MEDIUM,
                                    rule=p_rule,
                                    file=p_file,
                                    line=p_line,
                                    message=p_msg,
                                    fix="Alinear la definición del protobuf con las guías oficiales de estilo de Buf.",
                                )
                            )
                        except Exception:
                            pass
                else:
                    logs.append(f"[Aviso] Buf finalizó con código {buf_res.exit_code}: {buf_res.output[:200]}")

        log_file.write_text("\n\n".join(logs), encoding="utf-8")

        has_high_or_crit = any(f.severity in (Severity.CRITICAL, Severity.HIGH) for f in findings)
        if has_high_or_crit:
            status = StageStatus.FAIL
            err_msg = f"Se detectaron fallos de conformidad en contratos ({len(findings)} hallazgos)"
        else:
            status = StageStatus.PASS
            err_msg = None

        result = StageResult(
            stage=self.stage_name,
            status=status,
            tool="contracts",
            tool_version="spectral/buf",
            exit_code=0 if status == StageStatus.PASS else 1,
            duration_s=round(total_duration, 2),
            findings=findings,
            reports=reports,
            error_message=err_msg,
            log_file=str(log_file),
            metadata={
                "openapi_file": openapi_file,
                "events_files": events_files,
                "proto_files": proto_files,
                "violations_count": len(findings),
            },
        )

        (stage_out / "result.json").write_text(
            json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return result

    def _parse_json(self, json_file: Path, contract_rel_path: str) -> List[Finding]:
        findings = []
        items = json.loads(json_file.read_text(encoding="utf-8"))
        for idx, item in enumerate(items):
            code = item.get("code", "spectral-rule")
            message = item.get("message", "Error de contrato OpenAPI")
            severity_num = item.get("severity", 1)  # 0=error, 1=warn, 2=info, 3=hint
            range_info = item.get("range", {}).get("start", {})
            line = range_info.get("line", 1) + 1

            if severity_num == 0:
                severity = Severity.HIGH
            elif severity_num == 1:
                severity = Severity.MEDIUM
            else:
                severity = Severity.LOW

            stable_id = f"contracts:{code}:{contract_rel_path}:{line}"
            findings.append(
                Finding(
                    id=stable_id,
                    stage=self.stage_name,
                    severity=severity,
                    rule=str(code),
                    file=contract_rel_path,
                    line=line,
                    message=message,
                    fix="Ajustar la definición del esquema OpenAPI de acuerdo a la especificación oficial.",
                )
            )
        return findings

    def get_mcp_server(self) -> MCPServer:
        server = MCPServer("mcp-contracts")

        @server.tool()
        def contracts_scan(project_path: str, output_dir: Optional[str] = None) -> str:
            """Ejecuta una auditoría de contratos OpenAPI/AsyncAPI/Protobuf."""
            out = output_dir or str(Path(project_path) / ".review" / "contracts")
            result = self.run(project_path, out)
            return json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False)

        @server.tool()
        def contracts_get_skill() -> str:
            """Obtiene la guía operativa (Skill) del agente para diseñar contratos OpenAPI conformes."""
            return self.get_skill()

        return server
