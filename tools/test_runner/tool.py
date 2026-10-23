import json
import os
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from mcp.server.mcpserver import MCPServer

from core.base_tool import BaseTool
from core.models import Finding, ProjectConfig, Severity, StageResult, StageStatus
from core.runner import DockerRunner
from core.versions import get_docker_image


_GO_DIRECTIVE_RE = re.compile(r"^go[ \t]+(\d+)\.(\d+)(?:\.\d+)?(?:(?:rc|beta)\d+)?[ \t]*(?://.*)?$")


def go_version_from_gomod(project_path: str) -> Optional[str]:
    """Devuelve "mayor.menor" de la directiva `go` del go.mod del servicio, o None si no se puede leer.

    Solo cuenta la directiva `go` (versión del lenguaje que exige el módulo). La línea `toolchain goX.Y.Z`
    es una sugerencia que la imagen oficial ignora (fija GOTOOLCHAIN=local), así que no decide la imagen.
    """
    go_mod = Path(project_path) / "go.mod"
    try:
        text = go_mod.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for raw_line in text.splitlines():
        m = _GO_DIRECTIVE_RE.match(raw_line.strip())
        if m:
            return f"{m.group(1)}.{m.group(2)}"
    return None


_PY_VERSION_RE = re.compile(r"(\d+)\.(\d+)")
_REQUIRES_PYTHON_RE = re.compile(r"""^requires-python\s*=\s*["']([^"']+)["']""", re.MULTILINE)
_POETRY_PYTHON_RE = re.compile(r"""^python\s*=\s*["']([^"']+)["']""", re.MULTILINE)


def python_version_from_project(project_path: str) -> Tuple[Optional[str], Optional[str]]:
    """Devuelve ("mayor.menor", origen) de la versión de Python que declara el servicio, o (None, None).

    Orden: `.python-version` (versión exacta del entorno) y luego `requires-python` de pyproject.toml
    (o `python = ...` de Poetry). De un rango se toma la cota inferior (`>=3.11,<4` -> 3.11): las pruebas
    corren con la versión mínima que el servicio dice soportar. Solo se aceptan versiones 3.x.
    """
    root = Path(project_path)
    candidates: List[Tuple[str, str]] = []
    try:
        candidates.append(((root / ".python-version").read_text(encoding="utf-8").strip(), ".python-version"))
    except OSError:
        pass
    try:
        text = (root / "pyproject.toml").read_text(encoding="utf-8", errors="replace")
        for rx in (_REQUIRES_PYTHON_RE, _POETRY_PYTHON_RE):
            m = rx.search(text)
            if m:
                spec = m.group(1)
                lower = [part for part in spec.split(",") if not part.strip().startswith(("<", "!="))]
                candidates.append(((lower[0] if lower else spec), "pyproject.toml"))
                break
    except OSError:
        pass
    for raw, source in candidates:
        m = _PY_VERSION_RE.search(raw)
        if m and m.group(1) == "3":
            return f"{m.group(1)}.{m.group(2)}", source
    return None, None


def resolve_stack_image(stack_info: dict, project_path: str) -> Tuple[str, str]:
    """Elige la imagen del toolchain para un perfil de stack. Devuelve (imagen, origen).

    Orden: 1) versión declarada por el servicio (Go: directiva `go` de go.mod -> golang:<mayor.menor>;
    Python: .python-version o requires-python -> python:<mayor.menor>-slim);
    2) variable `image_env` del perfil resuelta en versions.env / entorno; 3) campo `image` del perfil.
    Lo usan las etapas `test` y `lint`, para que ambas corran con el mismo toolchain.
    """
    profile_image = stack_info.get("image", "alpine")
    if stack_info.get("image_from") == "go.mod":
        version = go_version_from_gomod(project_path)
        if version:
            return f"golang:{version}", "go.mod"
    if stack_info.get("image_from") == "python-version":
        version, source = python_version_from_project(project_path)
        if version:
            return f"python:{version}-slim", source
    image_env = stack_info.get("image_env")
    if image_env:
        return get_docker_image(image_env, profile_image), f"versions.env:{image_env}"
    return profile_image, "perfil"


class TestOutputParser:
    """Extrae métricas reales de pruebas y cobertura a partir de reportes generados o logs."""

    @staticmethod
    def parse_results(
        stack: str,
        stage_out: Path,
        stdout_output: str,
    ) -> Tuple[int, Optional[float]]:
        total_tests = 0
        coverage_pct: Optional[float] = None

        if stack in ("nestjs", "node", "frontend_react"):
            # 1. Total tests (Jest or node --test)
            m_tests = re.search(r"Tests:\s+.*?(\d+)\s+total", stdout_output)
            if m_tests:
                total_tests = int(m_tests.group(1))
            else:
                m_passed = re.search(r"Tests:\s+(\d+)\s+passed", stdout_output)
                if m_passed:
                    total_tests = int(m_passed.group(1))
                else:
                    # Support for node --test (TAP or spec format)
                    m_tap_pass = re.search(r"# pass\s+(\d+)", stdout_output)
                    m_tap_fail = re.search(r"# fail\s+(\d+)", stdout_output)
                    if m_tap_pass:
                        p = int(m_tap_pass.group(1))
                        f = int(m_tap_fail.group(1)) if m_tap_fail else 0
                        total_tests = p + f
                    else:
                        m_spec_pass = re.search(r"ℹ pass\s+(\d+)", stdout_output)
                        m_spec_fail = re.search(r"ℹ fail\s+(\d+)", stdout_output)
                        if m_spec_pass:
                            p = int(m_spec_pass.group(1))
                            f = int(m_spec_fail.group(1)) if m_spec_fail else 0
                            total_tests = p + f

            # 2. Coverage
            cov_summary = stage_out / "coverage" / "coverage-summary.json"
            if cov_summary.exists():
                try:
                    data = json.loads(cov_summary.read_text(encoding="utf-8"))
                    lines_pct = data.get("total", {}).get("lines", {}).get("pct")
                    if lines_pct is not None:
                        coverage_pct = float(lines_pct)
                except Exception:
                    pass
            if coverage_pct is None:
                m_table = re.search(r"All files\s+\|\s*[\d\.]+\s*\|\s*[\d\.]+\s*\|\s*[\d\.]+\s*\|\s*([\d\.]+)", stdout_output)
                if m_table:
                    coverage_pct = float(m_table.group(1))

        elif stack == "java_springboot":
            # 1. Total tests
            surefire_dir = stage_out / "target" / "surefire-reports"
            found_xml = False
            xml_sum = 0
            if surefire_dir.exists():
                for xf in surefire_dir.glob("TEST-*.xml"):
                    try:
                        import xml.etree.ElementTree as ET
                        root = ET.parse(xf).getroot()
                        xml_sum += int(root.attrib.get("tests", 0))
                        found_xml = True
                    except Exception:
                        pass
            if found_xml:
                total_tests = xml_sum
            else:
                matches = re.findall(r"Tests run:\s*(\d+),\s*Failures:\s*(\d+),\s*Errors:\s*(\d+)", stdout_output)
                if matches:
                    total_tests = int(matches[-1][0])

            # 2. Coverage JaCoCo
            jacoco_file = stage_out / "target" / "site" / "jacoco" / "jacoco.xml"
            if jacoco_file.exists():
                try:
                    import xml.etree.ElementTree as ET
                    root = ET.parse(jacoco_file).getroot()
                    for counter in root.findall("counter"):
                        if counter.attrib.get("type") == "LINE":
                            missed = int(counter.attrib.get("missed", 0))
                            covered = int(counter.attrib.get("covered", 0))
                            if (missed + covered) > 0:
                                coverage_pct = round((covered / (missed + covered)) * 100, 2)
                                break
                except Exception:
                    pass

        elif stack == "ruby":
            # 1. Total tests
            m_rspec = re.search(r"(\d+)\s+examples?,\s*(\d+)\s+failures?", stdout_output)
            if m_rspec:
                total_tests = int(m_rspec.group(1))

            # 2. Coverage SimpleCov
            simplecov_file = stage_out / "coverage" / ".last_run.json"
            if simplecov_file.exists():
                try:
                    data = json.loads(simplecov_file.read_text(encoding="utf-8"))
                    # SimpleCov >= 0.21 escribe {"result": {"line": N}}; versiones previas, "covered_percent".
                    res_cov = data.get("result", {}) or {}
                    raw_cov = res_cov.get("line", res_cov.get("covered_percent"))
                    coverage_pct = float(raw_cov) if raw_cov is not None else None
                except Exception:
                    pass

        elif stack == "go":
            # 1. Total tests
            pass_c = len(re.findall(r"---\s+PASS:\s+(\S+)", stdout_output))
            fail_c = len(re.findall(r"---\s+FAIL:\s+(\S+)", stdout_output))
            if pass_c + fail_c > 0:
                total_tests = pass_c + fail_c
            else:
                # Contar eventos JSON si se corrió con go test -json
                json_passes = len(re.findall(r'"Action":"pass","Test":"[^"]+"', stdout_output))
                json_fails = len(re.findall(r'"Action":"fail","Test":"[^"]+"', stdout_output))
                if json_passes + json_fails > 0:
                    total_tests = json_passes + json_fails
                else:
                    # Contar paquetes exitosos con ok si no se corrió con -v
                    m_ok_only = len(re.findall(r"^\s*ok\s+(\S+)", stdout_output, re.MULTILINE))
                    if m_ok_only > 0:
                        total_tests = m_ok_only

            # 2. Coverage total (go tool cover -func)
            m_tot_cov = re.search(r"total:\s*\(statements\)\s*([\d\.]+)%", stdout_output)
            if m_tot_cov:
                coverage_pct = float(m_tot_cov.group(1))
            else:
                # Si no hay línea total:, nunca inventar 0.0
                coverage_pct = None

        elif stack in ("python", "python_mcp"):
            # 1. Total de pruebas: JUnit XML de pytest; si no existe, línea resumen de pytest.
            junit_file = stage_out / "junit.xml"
            if junit_file.exists():
                try:
                    import xml.etree.ElementTree as ET
                    root = ET.parse(junit_file).getroot()
                    suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
                    total_tests = sum(int(s_.attrib.get("tests", 0)) for s_ in suites)
                except Exception:
                    total_tests = 0
            if total_tests == 0:
                for line in reversed(stdout_output.splitlines()):
                    if re.search(r"\bin [\d\.]+s\b", line) and re.search(r"\d+ (?:passed|failed|errors?|skipped)", line):
                        counts = re.findall(r"(\d+) (passed|failed|errors?|skipped|xfailed|xpassed)", line)
                        total_tests = sum(int(n) for n, _ in counts)
                        break

            # 2. Cobertura: coverage.json de pytest-cov; si no existe, fila TOTAL del reporte en consola.
            cov_json = stage_out / "coverage.json"
            if cov_json.exists():
                try:
                    data = json.loads(cov_json.read_text(encoding="utf-8"))
                    pct = data.get("totals", {}).get("percent_covered")
                    if pct is not None:
                        coverage_pct = round(float(pct), 2)
                except Exception:
                    pass
            if coverage_pct is None:
                m_pycov = re.search(r"^TOTAL\s+.*?(\d+(?:\.\d+)?)%\s*$", stdout_output, re.MULTILINE)
                if m_pycov:
                    coverage_pct = float(m_pycov.group(1))

        return total_tests, coverage_pct


class TestRunnerTool(BaseTool):
    """Herramienta de ejecución de pruebas unitarias y cobertura agnóstica del stack."""

    @property
    def name(self) -> str:
        return "test_runner"

    @property
    def stage_name(self) -> str:
        return "test"

    @property
    def description(self) -> str:
        return "Ejecuta las pruebas unitarias y compila el proyecto verificando cobertura mínima."

    def detect_stack(self, project_path: str, config: Optional[ProjectConfig] = None) -> Optional[dict]:
        stacks_dir = Path(__file__).parent / "stacks"
        p_path = Path(project_path)

        # 1. Prioridad: stack explícito configurado
        if config and config.stack:
            target = stacks_dir / f"{config.stack}.json"
            if target.exists():
                return json.loads(target.read_text(encoding="utf-8"))

        # 2. Detección específica por contenido y framework
        pkg_json = p_path / "package.json"
        if pkg_json.exists():
            try:
                pkg_text = pkg_json.read_text(encoding="utf-8", errors="ignore")
                if "@nestjs/core" in pkg_text:
                    nest_prof = stacks_dir / "nestjs.json"
                    if nest_prof.exists():
                        return json.loads(nest_prof.read_text(encoding="utf-8"))
                if "react" in pkg_text:
                    react_prof = stacks_dir / "frontend_react.json"
                    if react_prof.exists():
                        return json.loads(react_prof.read_text(encoding="utf-8"))
            except Exception:
                pass

        # Java Spring Boot
        if (p_path / "pom.xml").exists() or (p_path / "build.gradle").exists() or (p_path / "build.gradle.kts").exists():
            java_prof = stacks_dir / "java_springboot.json"
            if java_prof.exists():
                return json.loads(java_prof.read_text(encoding="utf-8"))

        # Ruby
        if (p_path / "Gemfile").exists():
            ruby_prof = stacks_dir / "ruby.json"
            if ruby_prof.exists():
                return json.loads(ruby_prof.read_text(encoding="utf-8"))

        # Go
        if (p_path / "go.mod").exists():
            go_prof = stacks_dir / "go.json"
            if go_prof.exists():
                return json.loads(go_prof.read_text(encoding="utf-8"))

        # Python / Python MCP
        py_files = [p_path / n for n in ("pyproject.toml", "requirements.txt", "setup.py", "setup.cfg", "Pipfile")]
        for pyf in py_files:
            if pyf.exists():
                try:
                    py_text = pyf.read_text(encoding="utf-8", errors="ignore")
                    if "mcp" in py_text.lower() or "fastmcp" in py_text.lower():
                        mcp_prof = stacks_dir / "python_mcp.json"
                        if mcp_prof.exists():
                            return json.loads(mcp_prof.read_text(encoding="utf-8"))
                except Exception:
                    pass
                py_prof = stacks_dir / "python.json"
                if py_prof.exists():
                    return json.loads(py_prof.read_text(encoding="utf-8"))

        # 3. Fallback genérico por archivos
        for profile in stacks_dir.glob("*.json"):
            try:
                data = json.loads(profile.read_text(encoding="utf-8"))
                for df in data.get("detection_files", []):
                    if (p_path / df).exists():
                        return data
            except Exception:
                continue
        return None

    def run(
        self,
        project_path: str,
        run_dir: str,
        config: Optional[ProjectConfig] = None,
    ) -> StageResult:
        stage_out = Path(run_dir) / self.stage_name
        stage_out.mkdir(parents=True, exist_ok=True)
        log_file = stage_out / "stage.log"

        stack_info = self.detect_stack(project_path, config)
        if not stack_info:
            return StageResult(
                stage=self.stage_name,
                status=StageStatus.SKIPPED,
                tool="test-runner",
                error_message="No se detectó un stack soportado para pruebas unitarias",
            )

        image, image_source = resolve_stack_image(stack_info, project_path)
        test_cmd = stack_info.get("test_cmd", "true")
        cache_path = stack_info.get("cache_path", "/tmp/cache")
        stack_name = stack_info.get("stack", "generic")

        # D5: Aislamiento estricto de código: montar project_path en :ro y copiar a volumen efímero
        work_volume = f"review-work-{os.urandom(4).hex()}"
        subprocess.run(["docker", "volume", "create", work_volume], check=True, capture_output=True)

        try:
            volumes = {
                project_path: "/src_ro:ro",
                work_volume: "/src:rw",
                str(stage_out): "/out:rw",
                f"review-cache-{stack_name}": cache_path,
            }

            copy_cmd = (
                "tar -C /src_ro "
                "--exclude='node_modules' "
                "--exclude='vendor' "
                "--exclude='target' "
                "--exclude='coverage' "
                "--exclude='dist' "
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
            container_cmd = (
                f"{copy_cmd} && cd /src && ({test_cmd}); EC=$?; "
                "[ -d /src/coverage ] && cp -r /src/coverage /out/ 2>/dev/null || true; "
                "[ -d /src/target ] && cp -r /src/target /out/ 2>/dev/null || true; "
                "exit $EC"
            )

            res = DockerRunner.run_container(
                image=image,
                command=container_cmd,
                volumes=volumes,
                timeout_seconds=900,
            )
        finally:
            subprocess.run(["docker", "volume", "rm", "-f", work_volume], capture_output=True)

        log_file.write_text(res.output, encoding="utf-8")

        total_tests, coverage_pct = TestOutputParser.parse_results(stack_name, stage_out, res.output)

        # Transparencia: archivos que el perfil dejó fuera de la cobertura por ser código generado.
        coverage_excluded: List[str] = []
        excluded_file = stage_out / "coverage.excluded"
        if excluded_file.exists():
            try:
                coverage_excluded = sorted(
                    ln.strip() for ln in excluded_file.read_text(encoding="utf-8").splitlines() if ln.strip()
                )
            except Exception:
                coverage_excluded = []

        findings: List[Finding] = []
        if res.exit_code in (124, 125):
            status = StageStatus.ERROR
            err_msg = f"Fallo al ejecutar contenedor de pruebas: {res.output[:300]}"
        elif res.exit_code != 0:
            status = StageStatus.FAIL
            err_msg = f"Las pruebas unitarias fallaron con código {res.exit_code}"
            findings.append(
                Finding(
                    id=f"test:unit_test_failure:{stack_name}",
                    stage=self.stage_name,
                    severity=Severity.HIGH,
                    rule="UNIT_TEST_FAILURE",
                    message="Una o más pruebas unitarias fallaron durante la ejecución.",
                    fix="Revisar el log de pruebas en stage.log y corregir las aserciones rotas.",
                )
            )
        else:
            status = StageStatus.PASS
            err_msg = None

        result = StageResult(
            stage=self.stage_name,
            status=status,
            tool=f"test-runner-{stack_name}",
            tool_version="1.0.0",
            exit_code=res.exit_code,
            duration_s=round(res.duration_s, 2),
            findings=findings,
            reports=[str(log_file)],
            error_message=err_msg,
            log_file=str(log_file),
            metadata={
                "stack": stack_name,
                "coverage_pct": coverage_pct,
                "total_tests": total_tests,
                "image": image,
                "image_source": image_source,
                "coverage_excluded_files": coverage_excluded,
            },
        )

        (stage_out / "result.json").write_text(
            json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        return result

    def get_mcp_server(self) -> MCPServer:
        server = MCPServer("mcp-test-runner")

        @server.tool()
        def test_runner_run(project_path: str, output_dir: Optional[str] = None) -> str:
            """Ejecuta la suite de pruebas del proyecto detectando su stack automáticamente."""
            out = output_dir or str(Path(project_path) / ".review" / "test")
            result = self.run(project_path, out)
            return json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False)

        @server.tool()
        def test_runner_get_skill() -> str:
            """Obtiene la guía operativa (Skill) del agente para depurar y escribir pruebas unitarias."""
            return self.get_skill()

        return server
