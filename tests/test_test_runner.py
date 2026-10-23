import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from core.gate import GateEvaluator
from core.models import Finding, ProjectConfig, RunVerdict, Severity, StageResult, StageStatus
from core.versions import get_docker_image
from tools.test_runner.tool import (
    TestOutputParser,
    TestRunnerTool,
    go_version_from_gomod,
    resolve_stack_image,
)

PIPELINE_ROOT = Path(__file__).resolve().parent.parent
GO_PROFILE = PIPELINE_ROOT / "tools" / "test_runner" / "stacks" / "go.json"
GO_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "go_generated"


def docker_image_available(image: str) -> bool:
    """True si Docker responde y la imagen ya está en el equipo (las pruebas no descargan imágenes).

    Con PIPELINE_SKIP_DOCKER_TESTS=1 se omiten las pruebas que levantan contenedores.
    """
    if os.environ.get("PIPELINE_SKIP_DOCKER_TESTS") == "1" or not shutil.which("docker"):
        return False
    try:
        proc = subprocess.run(["docker", "image", "inspect", image], capture_output=True, timeout=30)
    except Exception:
        return False
    return proc.returncode == 0


class TestTestRunnerParsing(unittest.TestCase):

    def test_parse_ruby_rspec_output(self):
        log = """
        Tariff::Application::TariffService
          finds an account by lot with an indexed lookup (TRF-02)

        Finished in 0.44323 seconds (files took 6.8 seconds to load)
        51 examples, 0 failures
        """
        with TemporaryDirectory() as tmpdir:
            total_tests, cov = TestOutputParser.parse_results("ruby", Path(tmpdir), log)
            self.assertEqual(total_tests, 51)
            self.assertIsNone(cov)

    def test_parse_java_maven_output(self):
        log = """
        [INFO] Results:
        [INFO]
        [INFO] Tests run: 10, Failures: 0, Errors: 0, Skipped: 0
        [INFO]
        [INFO] BUILD SUCCESS
        """
        with TemporaryDirectory() as tmpdir:
            total_tests, cov = TestOutputParser.parse_results("java_springboot", Path(tmpdir), log)
            self.assertEqual(total_tests, 10)
            self.assertIsNone(cov)

    def test_parse_nestjs_jest_output(self):
        log = """
        ------------------------------------|---------|----------|---------|---------|
        File                                | % Stmts | % Branch | % Funcs | % Lines |
        ------------------------------------|---------|----------|---------|---------|
        All files                           |   32.54 |    27.39 |    34.9 |   32.98 |
        ------------------------------------|---------|----------|---------|---------|
        Test Suites: 1 failed, 5 passed, 6 total
        Tests:       19 passed, 19 total
        Snapshots:   0 total
        Time:        45.726 s
        """
        with TemporaryDirectory() as tmpdir:
            total_tests, cov = TestOutputParser.parse_results("nestjs", Path(tmpdir), log)
            self.assertEqual(total_tests, 19)
            self.assertEqual(cov, 32.98)

    def test_parse_go_output_from_vessel_stage_log(self):
        log = """
        ?   	github.com/terminal-portuaria/vessel-berth-service/internal/application/dto	[no test files]
        ?   	github.com/terminal-portuaria/vessel-berth-service/internal/domain/repository	[no test files]
        	github.com/terminal-portuaria/vessel-berth-service/internal/infrastructure/persistence/postgres		coverage: 0.0% of statements
        ok  	github.com/terminal-portuaria/vessel-berth-service/internal/application/service	1.009s	coverage: 48.7% of statements
        ok  	github.com/terminal-portuaria/vessel-berth-service/internal/domain/model	1.008s	coverage: 83.7% of statements
        """
        with TemporaryDirectory() as tmpdir:
            total_tests, cov = TestOutputParser.parse_results("go", Path(tmpdir), log)
            self.assertEqual(total_tests, 2)
            self.assertIsNone(cov)

    def test_parse_go_output_with_v_and_func_coverage(self):
        log = """
        === RUN   TestAllocateBerth
        --- PASS: TestAllocateBerth (0.01s)
        === RUN   TestReleaseBerth
        --- PASS: TestReleaseBerth (0.01s)
        PASS
        coverage: 6.9% of statements
        github.com/terminal-portuaria/vessel-berth-service/internal/domain/model/berth.go:15:	Allocate	100.0%
        total:	(statements)	6.9%
        """
        with TemporaryDirectory() as tmpdir:
            total_tests, cov = TestOutputParser.parse_results("go", Path(tmpdir), log)
            self.assertEqual(total_tests, 2)
            self.assertEqual(cov, 6.9)

    def test_parse_node_test_tap_output(self):
        log = """
        # Subtest: Dashboard endpoints
        ok 1 - returns metrics
        # Subtest: User management
        ok 2 - validates payload
        # tests 2
        # pass 2
        # fail 0
        # duration_ms 120.4
        """
        with TemporaryDirectory() as tmpdir:
            total_tests, cov = TestOutputParser.parse_results("node", Path(tmpdir), log)
            self.assertEqual(total_tests, 2)
            self.assertIsNone(cov)

    def _ruby_coverage(self, last_run: object) -> object:
        log = "51 examples, 0 failures"
        with TemporaryDirectory() as tmpdir:
            cov_dir = Path(tmpdir) / "coverage"
            cov_dir.mkdir()
            content = last_run if isinstance(last_run, str) else json.dumps(last_run)
            (cov_dir / ".last_run.json").write_text(content, encoding="utf-8")
            total_tests, cov = TestOutputParser.parse_results("ruby", Path(tmpdir), log)
            self.assertEqual(total_tests, 51)
            return cov

    def test_parse_ruby_simplecov_line_format(self):
        # D24: SimpleCov >= 0.21 escribe {"result": {"line": N}}
        self.assertEqual(self._ruby_coverage({"result": {"line": 90.74}}), 90.74)

    def test_parse_ruby_simplecov_legacy_covered_percent(self):
        # D24: versiones previas escriben {"result": {"covered_percent": N}}
        self.assertEqual(self._ruby_coverage({"result": {"covered_percent": 77.5}}), 77.5)

    def test_parse_ruby_simplecov_line_wins_over_covered_percent(self):
        self.assertEqual(self._ruby_coverage({"result": {"line": 81.0, "covered_percent": 12.0}}), 81.0)

    def test_parse_ruby_simplecov_zero_is_measured_not_missing(self):
        # Un 0 real es una medición (bloquea por umbral), no "no medida".
        self.assertEqual(self._ruby_coverage({"result": {"line": 0}}), 0.0)

    def test_parse_ruby_simplecov_without_known_keys_is_none(self):
        # D24: sin `line` ni `covered_percent` la cobertura es "no medida", nunca 0.0
        self.assertIsNone(self._ruby_coverage({"result": {"branch": 50.0}}))
        self.assertIsNone(self._ruby_coverage({"result": {}}))
        self.assertIsNone(self._ruby_coverage({}))
        self.assertIsNone(self._ruby_coverage({"result": None}))
        self.assertIsNone(self._ruby_coverage("esto no es json"))


class TestGoImageResolution(unittest.TestCase):
    """La imagen de Go sale de la directiva `go` del go.mod; si no se puede leer, de versions.env (IMG_GOLANG)."""

    def setUp(self):
        self.profile = json.loads(GO_PROFILE.read_text(encoding="utf-8"))
        self.default_image = get_docker_image("IMG_GOLANG", "sin-definir")

    def _resolve(self, go_mod: object):
        with TemporaryDirectory() as tmpdir:
            if go_mod is not None:
                (Path(tmpdir) / "go.mod").write_text(go_mod, encoding="utf-8")
            return go_version_from_gomod(tmpdir), resolve_stack_image(self.profile, tmpdir)

    def test_versions_env_defines_default_go_image(self):
        self.assertTrue(self.default_image.startswith("golang:"), self.default_image)
        self.assertEqual(self.profile.get("image_env"), "IMG_GOLANG")

    def test_go_directive_major_minor(self):
        version, (image, source) = self._resolve("module example.com/svc\n\ngo 1.22\n")
        self.assertEqual(version, "1.22")
        self.assertEqual((image, source), ("golang:1.22", "go.mod"))

    def test_go_directive_with_patch(self):
        version, (image, source) = self._resolve("module example.com/svc\n\ngo 1.26.0\n\nrequire (\n\tgithub.com/google/uuid v1.6.0\n)\n")
        self.assertEqual(version, "1.26")
        self.assertEqual((image, source), ("golang:1.26", "go.mod"))

    def test_go_directive_with_crlf_and_comment(self):
        version, (image, _) = self._resolve("module example.com/svc\r\n\r\ngo 1.24.3 // minimo exigido\r\n")
        self.assertEqual(version, "1.24")
        self.assertEqual(image, "golang:1.24")

    def test_toolchain_line_does_not_decide_image(self):
        # `toolchain` es una sugerencia que la imagen oficial ignora (GOTOOLCHAIN=local): manda la directiva `go`.
        version, (image, source) = self._resolve("module example.com/svc\n\ngo 1.22.0\n\ntoolchain go1.23.4\n")
        self.assertEqual(version, "1.22")
        self.assertEqual((image, source), ("golang:1.22", "go.mod"))

    def test_toolchain_without_go_directive_falls_back(self):
        version, (image, source) = self._resolve("module example.com/svc\n\ntoolchain go1.23.4\n")
        self.assertIsNone(version)
        self.assertEqual((image, source), (self.default_image, "versions.env:IMG_GOLANG"))

    def test_missing_directive_falls_back_to_versions_env(self):
        # Un requerimiento llamado "go..." o un comentario no son la directiva.
        go_mod = "module example.com/svc\n\n// go 1.19\nrequire (\n\tgo.uber.org/zap v1.27.0\n)\n"
        version, (image, source) = self._resolve(go_mod)
        self.assertIsNone(version)
        self.assertEqual((image, source), (self.default_image, "versions.env:IMG_GOLANG"))

    def test_missing_go_mod_falls_back_to_versions_env(self):
        version, (image, source) = self._resolve(None)
        self.assertIsNone(version)
        self.assertEqual((image, source), (self.default_image, "versions.env:IMG_GOLANG"))

    def test_profile_image_is_last_resort(self):
        with TemporaryDirectory() as tmpdir:
            profile = {"stack": "go", "image": "golang:9.99", "image_env": "IMG_NO_EXISTE", "image_from": "go.mod"}
            self.assertEqual(resolve_stack_image(profile, tmpdir), ("golang:9.99", "versions.env:IMG_NO_EXISTE"))
            self.assertEqual(resolve_stack_image({"image": "ruby:3.3"}, tmpdir), ("ruby:3.3", "perfil"))

    def _captured_image(self, tool, go_mod: str) -> str:
        from core.runner import CommandResult
        with TemporaryDirectory() as tmpdir:
            proj = Path(tmpdir) / "proj"
            proj.mkdir()
            (proj / "go.mod").write_text(go_mod, encoding="utf-8")
            with patch("core.runner.DockerRunner.run_container") as mock_run, patch("subprocess.run"):
                mock_run.return_value = CommandResult(exit_code=0, stdout="", stderr="", duration_s=0.1)
                res = tool.run(str(proj), str(Path(tmpdir) / "run"))
            self.assertEqual(res.metadata.get("image_source"), "go.mod")
            self.assertEqual(res.metadata.get("image"), mock_run.call_args.kwargs["image"])
            return mock_run.call_args.kwargs["image"]

    def test_test_stage_runs_with_image_from_go_mod(self):
        self.assertEqual(self._captured_image(TestRunnerTool(), "module m\n\ngo 1.22\n"), "golang:1.22")

    def test_lint_stage_runs_with_image_from_go_mod(self):
        from tools.lint.tool import LintTool
        self.assertEqual(self._captured_image(LintTool(), "module m\n\ngo 1.26.0\n"), "golang:1.26")


class TestGoCoverageProfile(unittest.TestCase):
    """D25: el perfil de Go excluye el código generado del perfil de cobertura antes de `go tool cover -func`."""

    def setUp(self):
        self.profile = json.loads(GO_PROFILE.read_text(encoding="utf-8"))
        self.cmd = self.profile["test_cmd"]

    def test_filter_runs_between_raw_profile_and_cover_func(self):
        raw = self.cmd.index("-coverprofile=/out/coverage.raw")
        filtered = self.cmd.index("> /out/coverage.out")
        report = self.cmd.index("go tool cover -func=/out/coverage.out")
        self.assertLess(raw, filtered)
        self.assertLess(filtered, report)
        # El total nunca se calcula sobre el perfil sin filtrar.
        self.assertNotIn("-func=/out/coverage.raw", self.cmd)

    def test_filter_excludes_pb_go_by_name(self):
        self.assertIn(r"f ~ /\.pb\.go$/", self.cmd)

    def test_filter_uses_official_generated_marker_before_package(self):
        # Regex oficial de Go: ^// Code generated .* DO NOT EDIT\.$ (aquí escapada para awk)
        marker = r"/^\/\/ Code generated .* DO NOT EDIT\.$/"
        self.assertIn(marker, self.cmd)
        # La búsqueda se corta en la cláusula package: la regla de package aparece antes que la de la marca.
        self.assertLess(self.cmd.index("/^package[ \\t]/{d=1;next}"), self.cmd.index(marker))

    def test_profile_has_no_failure_masking(self):
        # Criterio 11 de CORRECCIONES_PIPELINE.md. (El único `||` del comando es el "o" lógico dentro de awk.)
        for forbidden in ("2>/dev/null", "--if-present", "--passWithNoTests", "|| true"):
            self.assertNotIn(forbidden, self.cmd)
        self.assertEqual(self.cmd.count("||"), 1)
        self.assertIn(r"f ~ /\.pb\.go$/ || (f in g)", self.cmd)

    def test_parse_go_output_with_exclusion_banner(self):
        log = """
        --- PASS: TestAdd (0.00s)
        PASS
        coverage: 25.0% of statements
        ok  	example.com/fixture/gogenerated	1.012s	coverage: 25.0% of statements
        === COBERTURA: ARCHIVOS GENERADOS EXCLUIDOS ===
        example.com/fixture/gogenerated/api.pb.go
        example.com/fixture/gogenerated/zz_generated.go
        === COBERTURA: TOTAL SIN CODIGO GENERADO ===
        example.com/fixture/gogenerated/calc.go:5:		Add		100.0%
        example.com/fixture/gogenerated/late_marker.go:8:	Unused		0.0%
        total:							(statements)	50.0%
        """
        with TemporaryDirectory() as tmpdir:
            total_tests, cov = TestOutputParser.parse_results("go", Path(tmpdir), log)
            self.assertEqual(total_tests, 1)
            # Manda el total filtrado de `go tool cover -func`, no el "coverage: 25.0%" de go test.
            self.assertEqual(cov, 50.0)

    def test_go_profile_end_to_end_in_container(self):
        """Corre el perfil real sobre tests/fixtures/go_generated (4 sentencias: 1 cubierta, 1 sin cubrir,
        1 en archivo con marca de generado, 1 en *.pb.go). Sin filtro daría 25 %; con filtro, 50 %."""
        image, source = resolve_stack_image(self.profile, str(GO_FIXTURE))
        self.assertEqual(source, "go.mod")
        if not docker_image_available(image):
            self.skipTest(f"Docker o la imagen {image} no están disponibles en este equipo")
        with TemporaryDirectory() as tmpdir:
            res = TestRunnerTool().run(str(GO_FIXTURE), tmpdir)
            log = (Path(tmpdir) / "test" / "stage.log").read_text(encoding="utf-8")
            self.assertEqual(res.status, StageStatus.PASS, log[-2000:])
            self.assertEqual(res.metadata["total_tests"], 1)
            self.assertEqual(res.metadata["coverage_pct"], 50.0, log[-2000:])
            self.assertEqual(
                res.metadata["coverage_excluded_files"],
                [
                    "example.com/fixture/gogenerated/api.pb.go",
                    "example.com/fixture/gogenerated/zz_generated.go",
                ],
            )
            # late_marker.go (marca después de `package`) sigue contando como código sin cubrir.
            self.assertRegex(log, r"late_marker\.go:\d+:\s+Unused\s+0\.0%")
            self.assertNotRegex(log, r"zz_generated\.go:\d+:")
            self.assertNotRegex(log, r"api\.pb\.go:\d+:")


class TestGateCoverageAndTests(unittest.TestCase):

    def setUp(self):
        self.gate = GateEvaluator()
        self.config = ProjectConfig(name="test-svc", path=".", coverage_min=50)

    def _build_stages(self, test_stage: StageResult) -> dict:
        stages = {
            "secrets": StageResult(stage="secrets", status=StageStatus.PASS, tool="gitleaks"),
            "lint": StageResult(stage="lint", status=StageStatus.PASS, tool="lint"),
            "sast": StageResult(stage="sast", status=StageStatus.PASS, tool="semgrep"),
            "deps": StageResult(stage="deps", status=StageStatus.PASS, tool="trivy"),
            "dockerfile": StageResult(stage="dockerfile", status=StageStatus.PASS, tool="hadolint"),
            "contracts": StageResult(stage="contracts", status=StageStatus.PASS, tool="spectral"),
            "test": test_stage,
        }
        return stages

    def test_gate_blocks_on_unmeasured_coverage(self):
        test_stage = StageResult(
            stage="test",
            status=StageStatus.PASS,
            tool="test-runner",
            findings=[],
            metadata={"stack": "ruby", "total_tests": 51, "coverage_pct": None},
        )
        stages = self._build_stages(test_stage)
        verdict, exit_code, blocking, count, _ = self.gate.evaluate(stages, self.config)
        self.assertEqual(verdict, RunVerdict.REJECTED)
        self.assertEqual(exit_code, 1)
        self.assertTrue(any(f.rule == "COVERAGE_NOT_MEASURED" for f in blocking))

    def test_gate_blocks_on_coverage_below_threshold(self):
        test_stage = StageResult(
            stage="test",
            status=StageStatus.PASS,
            tool="test-runner",
            findings=[],
            metadata={"stack": "nestjs", "total_tests": 19, "coverage_pct": 32.98},
        )
        stages = self._build_stages(test_stage)
        verdict, exit_code, blocking, count, _ = self.gate.evaluate(stages, self.config)
        self.assertEqual(verdict, RunVerdict.REJECTED)
        self.assertEqual(exit_code, 1)
        self.assertTrue(any(f.rule == "COVERAGE_MIN" for f in blocking))

    def test_gate_hardened_project_coverage_threshold(self):
        # Policy is 60%, project hardens to 75%
        config = ProjectConfig(name="test-svc", path=".", coverage_min=75)
        test_stage = StageResult(
            stage="test",
            status=StageStatus.PASS,
            tool="test-runner",
            findings=[],
            metadata={"stack": "node", "total_tests": 10, "coverage_pct": 65.0},
        )
        stages = self._build_stages(test_stage)
        verdict, exit_code, blocking, count, _ = self.gate.evaluate(stages, config)
        self.assertEqual(verdict, RunVerdict.REJECTED)
        self.assertTrue(any(f.rule == "COVERAGE_MIN" for f in blocking))
        self.assertEqual(test_stage.metadata.get("effective_coverage_min"), 75.0)

    def test_gate_blocks_on_zero_tests(self):
        test_stage = StageResult(
            stage="test",
            status=StageStatus.PASS,
            tool="test-runner",
            findings=[],
            metadata={"stack": "node", "total_tests": 0, "coverage_pct": 80.0},
        )
        stages = self._build_stages(test_stage)
        verdict, exit_code, blocking, count, _ = self.gate.evaluate(stages, self.config)
        self.assertEqual(verdict, RunVerdict.REJECTED)
        self.assertTrue(any(f.rule == "REQUIRE_TESTS" for f in blocking))

    def test_gate_blocks_on_missing_stage(self):
        # Falta 'contracts' y 'test'
        stages = {
            "secrets": StageResult(stage="secrets", status=StageStatus.PASS, tool="gitleaks"),
            "lint": StageResult(stage="lint", status=StageStatus.PASS, tool="lint"),
            "sast": StageResult(stage="sast", status=StageStatus.PASS, tool="semgrep"),
            "deps": StageResult(stage="deps", status=StageStatus.PASS, tool="trivy"),
            "dockerfile": StageResult(stage="dockerfile", status=StageStatus.PASS, tool="hadolint"),
        }
        verdict, exit_code, blocking, count, _ = self.gate.evaluate(stages, self.config)
        self.assertEqual(verdict, RunVerdict.ERROR)
        self.assertEqual(exit_code, 2)
        missing_rules = [f.rule for f in blocking if f.rule == "STAGE_MISSING"]
        self.assertEqual(len(missing_rules), 2)

    def test_gate_blocks_on_unexpected_skipped_stage(self):
        stages = {
            "secrets": StageResult(stage="secrets", status=StageStatus.PASS, tool="gitleaks"),
            "lint": StageResult(stage="lint", status=StageStatus.SKIPPED, tool="lint", error_message="El servicio no declara script 'lint'"),
            "sast": StageResult(stage="sast", status=StageStatus.PASS, tool="semgrep"),
            "deps": StageResult(stage="deps", status=StageStatus.PASS, tool="trivy"),
            "dockerfile": StageResult(stage="dockerfile", status=StageStatus.PASS, tool="hadolint"),
            "contracts": StageResult(stage="contracts", status=StageStatus.PASS, tool="spectral"),
            "test": StageResult(stage="test", status=StageStatus.PASS, tool="test-runner", metadata={"total_tests": 10, "coverage_pct": 80.0}),
        }
        verdict, exit_code, blocking, count, _ = self.gate.evaluate(stages, self.config)
        self.assertEqual(verdict, RunVerdict.ERROR)
        self.assertEqual(exit_code, 2)
        self.assertTrue(any(f.rule == "STAGE_SKIPPED" for f in blocking))


if __name__ == "__main__":
    unittest.main()

