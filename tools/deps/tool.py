from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from mcp.server.mcpserver import MCPServer

from core.base_tool import BaseTool
from core.models import Finding, ProjectConfig, Severity, StageResult, StageStatus
from core.runner import DockerRunner
from core.versions import get_docker_image


class DepsTool(BaseTool):
    """Herramienta de escaneo de dependencias, CVEs, licencias y SBOM con Trivy."""

    @property
    def name(self) -> str:
        return "deps"

    @property
    def stage_name(self) -> str:
        return "deps"

    @property
    def description(self) -> str:
        return "Detecta vulnerabilidades (CVEs) en dependencias, analiza licencias y genera SBOM CycloneDX."

    @property
    def image(self) -> str:
        return get_docker_image("IMG_TRIVY", "aquasec/trivy:0.57.1")

    def run(
        self,
        project_path: str,
        run_dir: str,
        config: Optional[ProjectConfig] = None,
    ) -> StageResult:
        stage_out = Path(run_dir) / self.stage_name
        stage_out.mkdir(parents=True, exist_ok=True)
        report_json = stage_out / "report.json"
        sbom_file = stage_out / "sbom.cdx.json"
        log_file = stage_out / "stage.log"

        skip_dirs = "vendor,node_modules,target,dist,coverage,.git"
        container_cmd = (
            f"trivy fs --scanners vuln,license --skip-dirs {skip_dirs} --format json --output /out/report.json /src && "
            f"trivy fs --format cyclonedx --skip-dirs {skip_dirs} --output /out/sbom.cdx.json /src"
        )

        volumes = {
            project_path: "/src:ro",
            str(stage_out): "/out:rw",
            "review-trivy-db": "/root/.cache/trivy",
        }

        res = DockerRunner.run_container(
            image=self.image,
            command=["-c", container_cmd],
            entrypoint="sh",
            volumes=volumes,
            timeout_seconds=900,
        )

        log_file.write_text(res.output, encoding="utf-8")

        findings: List[Finding] = []
        reports: List[str] = []
        extra_metadata: Dict[str, Any] = {}

        parse_error = self.validate_execution(
            exit_code=res.exit_code,
            expected_exit_codes=(0,),
            report_file=report_json,
            tool_name="Trivy",
            output_snippet=res.output,
        )

        if not parse_error:
            reports.append(str(report_json))
            try:
                findings, extra_metadata = self._parse_json(report_json)
            except Exception as ex:
                parse_error = f"Error al procesar JSON de Trivy: {ex}"

        if sbom_file.exists():
            reports.append(str(sbom_file))

        if parse_error:
            status = StageStatus.ERROR
            err_msg = parse_error
        else:
            blocking_count = sum(1 for f in findings if f.severity in (Severity.CRITICAL, Severity.HIGH))
            if blocking_count > 0:
                status = StageStatus.FAIL
                err_msg = f"Se detectaron {len(findings)} CVEs/licencias ({blocking_count} bloqueantes)"
            else:
                status = StageStatus.PASS
                err_msg = None

        meta = {
            "findings_count": len(findings),
            "has_sbom": sbom_file.exists(),
        }
        meta.update(extra_metadata)

        result = StageResult(
            stage=self.stage_name,
            status=status,
            tool="trivy",
            tool_version="0.57.1",
            exit_code=res.exit_code,
            duration_s=round(res.duration_s, 2),
            findings=findings,
            reports=reports,
            error_message=err_msg,
            log_file=str(log_file),
            metadata=meta,
        )

        (stage_out / "result.json").write_text(
            json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        return result

    def _parse_json(
        self,
        json_file: Path,
        denied_licenses: Optional[List[str]] = None,
    ) -> Tuple[List[Finding], Dict[str, Any]]:
        if denied_licenses is None:
            denied_licenses = ["AGPL-3.0", "SSPL-1.0"]

        findings: List[Finding] = []
        data = json.loads(json_file.read_text(encoding="utf-8"))
        results = data.get("Results", [])

        total_licenses_seen = 0
        licenses_seen_set = set()

        for res in results:
            target = res.get("Target", "unknown")

            # 1. Vulnerabilidades
            for vuln in res.get("Vulnerabilities", []):
                vuln_id = vuln.get("VulnerabilityID", "CVE-UNKNOWN")
                pkg_name = vuln.get("PkgName", "unknown")
                inst_ver = vuln.get("InstalledVersion", "0")
                fixed_ver = vuln.get("FixedVersion")
                title = vuln.get("Title") or vuln.get("Description") or "Vulnerabilidad en dependencia"
                trivy_sev = vuln.get("Severity", "UNKNOWN").upper()

                if trivy_sev == "CRITICAL":
                    sev = Severity.CRITICAL
                elif trivy_sev == "HIGH":
                    sev = Severity.HIGH
                elif trivy_sev == "MEDIUM":
                    sev = Severity.MEDIUM
                elif trivy_sev == "LOW":
                    sev = Severity.LOW
                else:
                    sev = Severity.INFO

                # D7: Formato estable: deps:<CVE>:<paquete>@<versión>
                stable_id = f"deps:{vuln_id}:{pkg_name}@{inst_ver}"

                fix_msg = f"Actualizar {pkg_name} a versión {fixed_ver} o superior." if fixed_ver else None

                findings.append(
                    Finding(
                        id=stable_id,
                        stage=self.stage_name,
                        severity=sev,
                        rule=vuln_id,
                        file=target,
                        message=f"{pkg_name}@{inst_ver}: {title}",
                        fix=fix_msg,
                    )
                )

            # 2. Licencias
            for lic in res.get("Licenses", []):
                total_licenses_seen += 1
                lic_name = lic.get("Name", "UNKNOWN")
                pkg_name = lic.get("PkgName", "unknown")
                licenses_seen_set.add(lic_name)

                # D9: Bloquear las denegadas; no listar el resto como hallazgos, resumirlas en metadatos
                if lic_name in denied_licenses:
                    stable_id = f"deps:license-denied:{lic_name}:{pkg_name}"
                    findings.append(
                        Finding(
                            id=stable_id,
                            stage=self.stage_name,
                            severity=Severity.HIGH,
                            rule=f"DENIED_LICENSE_{lic_name}",
                            file=target,
                            message=f"Dependencia '{pkg_name}' utiliza una licencia denegada por política ({lic_name})",
                            fix=f"Reemplazar '{pkg_name}' por una alternativa con licencia permisiva autorizada",
                        )
                    )

        extra_metadata = {
            "total_dependencies_licenses": total_licenses_seen,
            "unique_licenses": sorted(list(licenses_seen_set)),
        }
        return findings, extra_metadata

    def get_mcp_server(self) -> MCPServer:
        server = MCPServer("mcp-deps")

        @server.tool()
        def deps_scan(project_path: str, output_dir: Optional[str] = None) -> str:
            """Ejecuta una auditoría de dependencias, vulnerabilidades (CVE) y licencias con Trivy."""
            out = output_dir or str(Path(project_path) / ".review" / "deps")
            result = self.run(project_path, out)
            return json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False)

        @server.tool()
        def deps_get_skill() -> str:
            """Obtiene la guía operativa (Skill) del agente para investigar y actualizar dependencias vulnerables."""
            return self.get_skill()

        return server
