from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from typing import Dict, List, Optional
from mcp.server.mcpserver import MCPServer

from core.base_tool import BaseTool
from core.models import Finding, ProjectConfig, Severity, StageResult, StageStatus
from core.runner import DockerRunner
from tools.test_runner.tool import TestRunnerTool, resolve_stack_image


class LintTool(BaseTool):
    """Herramienta de validación de formato, estilo y análisis estático del lenguaje (Linter)."""

    @property
    def name(self) -> str:
        return "lint"

    @property
    def stage_name(self) -> str:
        return "lint"

    @property
    def description(self) -> str:
        return "Ejecuta los linters del lenguaje (rubocop, eslint, gofmt/vet, spotless, ruff + mypy) en un entorno aislado."

    def run(
        self,
        project_path: str,
        run_dir: str,
        config: Optional[ProjectConfig] = None,
    ) -> StageResult:
        stage_out = Path(run_dir) / self.stage_name
        stage_out.mkdir(parents=True, exist_ok=True)
        log_file = stage_out / "stage.log"

        # Reusar detección de stack de TestRunnerTool
        test_tool = TestRunnerTool()
        stack_info = test_tool.detect_stack(project_path, config)
        if not stack_info:
            return StageResult(
                stage=self.stage_name,
                status=StageStatus.SKIPPED,
                tool="lint",
                error_message="No se detectó un stack soportado para linter",
            )

        image, image_source = resolve_stack_image(stack_info, project_path)
        lint_cmd = stack_info.get("lint_cmd", "true")
        cache_path = stack_info.get("cache_path", "/tmp/cache")
        stack_name = stack_info.get("stack", "generic")

        # D19 & D20: Validar presencia de linters específicos por stack para no aprobar en falso ni correr innecesariamente
        if stack_name in ("nestjs", "node", "frontend_react"):
            pkg_json = Path(project_path) / "package.json"
            has_lint_script = False
            if pkg_json.exists():
                try:
                    pkg_data = json.loads(pkg_json.read_text(encoding="utf-8"))
                    scripts = pkg_data.get("scripts", {})
                    has_lint_script = "lint" in scripts
                except Exception:
                    pass
            if not has_lint_script:
                reason = "El servicio no declara script 'lint' en package.json"
                result = StageResult(
                    stage=self.stage_name,
                    status=StageStatus.SKIPPED,
                    tool="lint",
                    tool_version=stack_name,
                    error_message=reason,
                    log_file=str(log_file),
                    metadata={"stack": stack_name, "reason": reason},
                )
                log_file.write_text(reason, encoding="utf-8")
                (stage_out / "result.json").write_text(
                    json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                return result

        elif stack_name == "java_springboot":
            pom_file = Path(project_path) / "pom.xml"
            if pom_file.exists():
                pom_content = pom_file.read_text(encoding="utf-8", errors="replace")
                if "spotless-maven-plugin" not in pom_content:
                    reason = "El servicio no declara plugin de lint (spotless-maven-plugin no encontrado en pom.xml)"
                    result = StageResult(
                        stage=self.stage_name,
                        status=StageStatus.SKIPPED,
                        tool="lint",
                        tool_version=stack_name,
                        error_message=reason,
                        log_file=str(log_file),
                        metadata={"stack": stack_name, "reason": reason},
                    )
                    log_file.write_text(reason, encoding="utf-8")
                    (stage_out / "result.json").write_text(
                        json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )
                    return result


        # D5: Aislamiento estricto de código: montar project_path en :ro y copiar a volumen efímero
        work_volume = f"review-work-{os.urandom(4).hex()}"
        subprocess.run(["docker", "volume", "create", work_volume], check=True, capture_output=True)

        try:
            volumes = {
                project_path: "/src_ro:ro",
                work_volume: "/src:rw",
                str(stage_out): "/out:rw",
                f"review-cache-{stack_name}": cache_path,
                # Reglas base del pipeline (p. ej. ruff.toml de Python) para servicios sin configuración propia
                str(Path(__file__).parent / "rules"): "/rules:ro",
            }

            copy_cmd = (
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
                "--exclude='.mypy_cache' "
                "--exclude='.pytest_cache' "
                "--exclude='.ruff_cache' "
                "-cf - . | tar -C /src -xf -"
            )
            container_cmd = [
                "/bin/sh",
                "-c",
                f"{copy_cmd} && cd /src && {lint_cmd}",
            ]

            res = DockerRunner.run_container(
                image=image,
                command=container_cmd,
                volumes=volumes,
                timeout_seconds=900,
            )

            # Guardar log
            log_file.write_text(res.output, encoding="utf-8")

            findings: List[Finding] = []
            if res.exit_code == 0:
                status = StageStatus.PASS
                err_msg = None
            else:
                status = StageStatus.FAIL
                err_msg = f"El linter del stack finalizó con código de error {res.exit_code}"
                findings = self._parse_lint_output(res.output, stack_name)

            result = StageResult(
                stage=self.stage_name,
                status=status,
                tool="lint",
                tool_version=stack_name,
                exit_code=res.exit_code,
                duration_s=round(res.duration_s, 2),
                findings=findings,
                reports=[],
                error_message=err_msg,
                log_file=str(log_file),
                metadata={"lint_cmd": lint_cmd, "stack": stack_name, "image": image, "image_source": image_source},
            )

            (stage_out / "result.json").write_text(
                json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            return result

        finally:
            subprocess.run(["docker", "volume", "rm", "-f", work_volume], capture_output=True)

    @staticmethod
    def _parse_lint_output(output: str, stack_name: str) -> List[Finding]:
        import re
        findings: List[Finding] = []
        lines = output.splitlines()

        if stack_name == "go":
            in_unformatted = False
            for line in lines:
                sline = line.strip()
                if "=== GOFMT UNFORMATTED ===" in sline:
                    in_unformatted = True
                    continue
                if in_unformatted:
                    if sline.endswith(".go") and not any(ign in sline for ign in [".pb.go", "vesselv1"]):
                        clean_f = sline.replace("/src/", "")
                        findings.append(
                            Finding(
                                id=f"lint:go:gofmt:{clean_f}",
                                stage="lint",
                                severity=Severity.HIGH,
                                rule="GOFMT_UNFORMATTED",
                                file=clean_f,
                                line=1,
                                message=f"El archivo '{clean_f}' no cumple con el formato estándar de gofmt.",
                                fix=f"Ejecutar 'gofmt -w {clean_f}'",
                            )
                        )
                    elif sline and not sline.endswith(".go"):
                        in_unformatted = False

            # Buscar diagnósticos de go vet: ej. path/file.go:12:34: message
            for line in lines:
                m_vet = re.search(r"^([^:\n]+\.go):(\d+):(?:\d+:)?\s*(.+)$", line.strip())
                if m_vet:
                    f_path = m_vet.group(1).replace("/src/", "")
                    line_n = int(m_vet.group(2))
                    msg = m_vet.group(3)
                    findings.append(
                        Finding(
                            id=f"lint:go:vet:{f_path}:{line_n}",
                            stage="lint",
                            severity=Severity.HIGH,
                            rule="GO_VET",
                            file=f_path,
                            line=line_n,
                            message=msg,
                            fix="Corregir el diagnóstico reportado por go vet.",
                        )
                    )

        elif stack_name == "ruby":
            for line in lines:
                m_cop = re.search(r"^([^:\n]+\.rb):(\d+):(?:\d+:)?\s*([A-Za-z]):\s*(.+)$", line.strip())
                if m_cop:
                    f_path = m_cop.group(1).replace("/src/", "")
                    line_n = int(m_cop.group(2))
                    rule_tag = m_cop.group(3)
                    msg = m_cop.group(4)
                    findings.append(
                        Finding(
                            id=f"lint:ruby:rubocop:{f_path}:{line_n}",
                            stage="lint",
                            severity=Severity.HIGH,
                            rule=f"RUBOCOP_{rule_tag}",
                            file=f_path,
                            line=line_n,
                            message=msg,
                            fix=f"Ejecutar 'bundle exec rubocop -a {f_path}'",
                        )
                    )

        elif stack_name == "java_springboot":
            for line in lines:
                m_file = re.search(r"(?:\[ERROR\]|\s+)([^:\s\n]+\.java)", line.strip())
                if m_file and "spotless-maven-plugin" not in line:
                    clean_f = m_file.group(1).replace("/src/", "")
                    findings.append(
                        Finding(
                            id=f"lint:java:spotless:{clean_f}",
                            stage="lint",
                            severity=Severity.HIGH,
                            rule="SPOTLESS_FORMAT",
                            file=clean_f,
                            line=1,
                            message=f"El archivo '{clean_f}' presenta infracciones de formato de código Spotless.",
                            fix="Ejecutar './mvnw spotless:apply'",
                        )
                    )

        elif stack_name in ("python", "python_mcp"):
            findings.extend(LintTool._parse_python_lint(lines))

        elif stack_name in ("nestjs", "node", "frontend_react"):
            for line in lines:
                m_ts = re.search(r"^([^:\n]+?\.(?:ts|tsx|js|jsx))\((\d+),\d+\):\s*(error\s+\w+:\s*.+)$", line.strip())
                if m_ts:
                    f_path = m_ts.group(1).replace("/src/", "")
                    line_n = int(m_ts.group(2))
                    msg = m_ts.group(3)
                    findings.append(
                        Finding(
                            id=f"lint:node:tsc:{f_path}:{line_n}",
                            stage="lint",
                            severity=Severity.HIGH,
                            rule="TSC_ERROR",
                            file=f_path,
                            line=line_n,
                            message=msg,
                            fix="Corregir error de compilación o tipo TypeScript.",
                        )
                    )

        # Si no se detectaron hallazgos individuales específicos pero falló el comando,
        # buscar al menos algún archivo mencionado en las líneas de error para asignar 'file'
        if not findings:
            target_file = None
            for line in lines:
                m_any = re.search(r"(\S+\.(?:go|java|rb|ts|js|py))\b", line)
                if m_any:
                    target_file = m_any.group(1).replace("/src/", "")
                    break
            if not target_file:
                target_file = "repository"

            findings.append(
                Finding(
                    id=f"lint:{stack_name}:{target_file}:violation",
                    stage="lint",
                    severity=Severity.HIGH,
                    rule="LINT_FAILED",
                    file=target_file,
                    line=1,
                    message=f"El linter de {stack_name} reportó infracciones de formato o sintaxis.",
                    fix="Corregir las infracciones reportadas en stage.log.",
                )
            )

        return findings

    # Reglas de Ruff que indican un error real o un riesgo, no solo estilo.
    _PY_HIGH_PREFIXES = ("F", "E9", "B", "S", "ASYNC", "BLE", "PLE")

    @staticmethod
    def _parse_python_lint(lines: List[str]) -> List[Finding]:
        """Hallazgos de Ruff (check y format) y de mypy, con identificadores estables por regla, archivo y línea."""
        import re
        findings: List[Finding] = []
        section = "ruff"
        seen = set()

        def clean(path: str) -> str:
            path = path.strip().replace("\\", "/")
            for prefix in ("/src/", "./"):
                if path.startswith(prefix):
                    path = path[len(prefix):]
            return path

        for raw in lines:
            line = raw.strip()
            if line.startswith("=== RUFF FORMAT"):
                section = "format"
                continue
            if line.startswith("=== MYPY"):
                section = "mypy"
                continue

            if section == "ruff":
                # Formato "concise": ruta:línea:col: CÓDIGO [*] mensaje
                m = re.match(r"^(.+?\.pyi?):(\d+):(\d+):\s+([A-Z]+[0-9]+)\s+(?:\[\*\]\s+)?(.+)$", line)
                if m:
                    f_path, line_n, code, msg = clean(m.group(1)), int(m.group(2)), m.group(4), m.group(5)
                    fid = f"lint:python:ruff:{code}:{f_path}:{line_n}"
                    if fid in seen:
                        continue
                    seen.add(fid)
                    high = code.startswith(LintTool._PY_HIGH_PREFIXES)
                    findings.append(
                        Finding(
                            id=fid,
                            stage="lint",
                            severity=Severity.HIGH if high else Severity.MEDIUM,
                            rule=f"RUFF_{code}",
                            file=f_path,
                            line=line_n,
                            message=msg,
                            fix=f"Ver https://docs.astral.sh/ruff/rules/ ({code}); si es autocorregible: 'ruff check --fix {f_path}'",
                        )
                    )

            elif section == "format":
                # Ruff <= 0.x: "Would reformat: ruta"; versiones nuevas: "--> ruta:línea:col" bajo "unformatted:"
                m = re.match(r"^Would reformat:\s+(.+\.pyi?)$", line) or re.match(r"^-->\s+(.+?\.pyi?):\d+", line)
                if m:
                    f_path = clean(m.group(1))
                    if f"lint:python:format:{f_path}" in seen:
                        continue
                    seen.add(f"lint:python:format:{f_path}")
                    findings.append(
                        Finding(
                            id=f"lint:python:format:{f_path}",
                            stage="lint",
                            severity=Severity.MEDIUM,
                            rule="RUFF_FORMAT",
                            file=f_path,
                            line=1,
                            message=f"El archivo '{f_path}' no cumple el formato estándar (ruff format).",
                            fix=f"Ejecutar 'ruff format {f_path}'",
                        )
                    )

            elif section == "mypy":
                m = re.match(r"^(.+?\.pyi?):(\d+):(?:\d+:)?\s*error:\s*(.+?)(?:\s+\[([\w-]+)\])?$", line)
                if m:
                    f_path, line_n, msg, code = clean(m.group(1)), int(m.group(2)), m.group(3), m.group(4) or "error"
                    fid = f"lint:python:mypy:{code}:{f_path}:{line_n}"
                    if fid in seen:
                        continue
                    seen.add(fid)
                    findings.append(
                        Finding(
                            id=fid,
                            stage="lint",
                            severity=Severity.HIGH,
                            rule=f"MYPY_{code.upper().replace('-', '_')}",
                            file=f_path,
                            line=line_n,
                            message=msg,
                            fix="Corregir la anotación o el uso del tipo reportado por mypy.",
                        )
                    )
        return findings

    def get_mcp_server(self) -> MCPServer:
        server = MCPServer("mcp-lint")

        @server.tool()
        def lint_scan(project_path: str, output_dir: Optional[str] = None) -> str:
            """Ejecuta el linter del stack en el código fuente."""
            out = output_dir or str(Path(project_path) / ".review" / "lint")
            result = self.run(project_path, out)
            return json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False)

        @server.tool()
        def lint_get_skill() -> str:
            """Obtiene la guía operativa (Skill) del agente para ejecutar y corregir infracciones de linter."""
            return self.get_skill()

        return server
