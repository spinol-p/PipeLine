"""Pruebas del soporte de Python: perfiles, versión de imagen, parsers de lint y pruebas, y reglas SAST."""

import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from core.models import Severity, StageStatus
from tools.lint.tool import LintTool
from tools.sast.tool import SastTool
from tools.test_runner.tool import (
    TestOutputParser,
    TestRunnerTool,
    python_version_from_project,
    resolve_stack_image,
)

STACKS = Path(__file__).resolve().parent.parent / "tools" / "test_runner" / "stacks"


def _docker_image_available(image: str) -> bool:
    if os.environ.get("PIPELINE_SKIP_DOCKER_TESTS") == "1" or not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "image", "inspect", image], capture_output=True, timeout=30).returncode == 0
    except Exception:
        return False


class TestPythonProfiles(unittest.TestCase):
    def test_profiles_run_real_linters_and_coverage(self):
        for name in ("python", "python_mcp"):
            prof = json.loads((STACKS / f"{name}.json").read_text(encoding="utf-8"))
            # El lint debe revisar algo de verdad (antes era 'python -m py_compile' sin archivos).
            self.assertIn("ruff check", prof["lint_cmd"])
            self.assertIn("ruff format", prof["lint_cmd"])
            self.assertIn("/rules/python/ruff.toml", prof["lint_cmd"])
            # Las pruebas deben dejar reportes que el parser lee, y sin '||' que oculte fallos.
            self.assertIn("--junitxml=/out/junit.xml", prof["test_cmd"])
            self.assertIn("--cov-report=json:/out/coverage.json", prof["test_cmd"])
            self.assertNotIn("|| pytest", prof["test_cmd"])
            # Herramientas fijadas por versión.
            for pkg in ("ruff==", "pytest==", "pytest-cov=="):
                self.assertIn(pkg, prof["lint_cmd"] + prof["test_cmd"])
            self.assertEqual(prof["image_from"], "python-version")

    def test_baseline_ruff_config_exists(self):
        cfg = Path(__file__).resolve().parent.parent / "tools" / "lint" / "rules" / "python" / "ruff.toml"
        text = cfg.read_text(encoding="utf-8")
        for family in ('"F"', '"B"', '"S"', '"I"', '"UP"'):
            self.assertIn(family, text)

    def test_detects_setup_py_projects(self):
        with TemporaryDirectory() as d:
            Path(d, "setup.py").write_text("from setuptools import setup\nsetup()\n", encoding="utf-8")
            self.assertEqual(TestRunnerTool().detect_stack(d)["stack"], "python")


class TestPythonVersionResolution(unittest.TestCase):
    def _prof(self):
        return json.loads((STACKS / "python.json").read_text(encoding="utf-8"))

    def test_python_version_file_wins(self):
        with TemporaryDirectory() as d:
            Path(d, ".python-version").write_text("3.11.9\n", encoding="utf-8")
            Path(d, "pyproject.toml").write_text('[project]\nrequires-python = ">=3.10"\n', encoding="utf-8")
            self.assertEqual(python_version_from_project(d), ("3.11", ".python-version"))
            self.assertEqual(resolve_stack_image(self._prof(), d), ("python:3.11-slim", ".python-version"))

    def test_requires_python_lower_bound(self):
        with TemporaryDirectory() as d:
            Path(d, "pyproject.toml").write_text('[project]\nrequires-python = "<4,>=3.10"\n', encoding="utf-8")
            self.assertEqual(python_version_from_project(d), ("3.10", "pyproject.toml"))

    def test_poetry_python_constraint(self):
        with TemporaryDirectory() as d:
            Path(d, "pyproject.toml").write_text(
                '[tool.poetry.dependencies]\npython = "^3.12"\n', encoding="utf-8"
            )
            self.assertEqual(python_version_from_project(d), ("3.12", "pyproject.toml"))

    def test_without_declaration_uses_versions_env(self):
        with TemporaryDirectory() as d:
            Path(d, "requirements.txt").write_text("requests\n", encoding="utf-8")
            image, source = resolve_stack_image(self._prof(), d)
            self.assertEqual(source, "versions.env:IMG_PYTHON_STACK")
            self.assertTrue(image.startswith("python:"))


class TestPythonTestParsing(unittest.TestCase):
    def test_reads_junit_and_coverage_json(self):
        with TemporaryDirectory() as d:
            out = Path(d)
            (out / "junit.xml").write_text(
                '<testsuites><testsuite name="pytest" tests="7" failures="1"/></testsuites>', encoding="utf-8"
            )
            (out / "coverage.json").write_text(json.dumps({"totals": {"percent_covered": 81.23456}}), encoding="utf-8")
            self.assertEqual(TestOutputParser.parse_results("python", out, ""), (7, 81.23))

    def test_falls_back_to_console_summary(self):
        log = (
            "Name        Stmts   Miss Branch BrPart  Cover\n"
            "TOTAL         120     30     40      5    72%\n"
            "1 failed, 5 passed, 2 skipped in 0.42s\n"
        )
        with TemporaryDirectory() as d:
            self.assertEqual(TestOutputParser.parse_results("python_mcp", Path(d), log), (8, 72.0))

    def test_no_report_means_no_invented_coverage(self):
        with TemporaryDirectory() as d:
            self.assertEqual(TestOutputParser.parse_results("python", Path(d), "no tests ran in 0.01s"), (0, None))


class TestPythonLintParsing(unittest.TestCase):
    LOG = "\n".join([
        "=== RUFF: CONFIGURACION BASE DEL PIPELINE ===",
        "app/main.py:3:8: F401 [*] `os` imported but unused",
        "app/main.py:10:5: S602 `subprocess` call with `shell=True` identified, security issue",
        "app/models.py:1:1: I001 [*] Import block is un-sorted or un-formatted",
        "Found 3 errors.",
        "=== RUFF FORMAT ===",
        "Would reformat: app/models.py",
        "1 file would be reformatted, 4 files already formatted",
        "=== MYPY ===",
        'app/main.py:22:12: error: Incompatible return value type (got "str", expected "int")  [return-value]',
    ])

    def test_ruff_format_and_mypy_findings(self):
        findings = LintTool._parse_lint_output(self.LOG, "python")
        by_id = {f.id: f for f in findings}
        self.assertEqual(len(findings), 5)
        self.assertEqual(by_id["lint:python:ruff:F401:app/main.py:3"].severity, Severity.HIGH)
        self.assertEqual(by_id["lint:python:ruff:S602:app/main.py:10"].severity, Severity.HIGH)
        self.assertEqual(by_id["lint:python:ruff:I001:app/models.py:1"].severity, Severity.MEDIUM)
        self.assertEqual(by_id["lint:python:format:app/models.py"].rule, "RUFF_FORMAT")
        mypy = by_id["lint:python:mypy:return-value:app/main.py:22"]
        self.assertEqual((mypy.rule, mypy.line), ("MYPY_RETURN_VALUE", 22))

    def test_new_ruff_format_output(self):
        log = "\n".join([
            "=== RUFF FORMAT ===",
            "unformatted: File would be reformatted",
            " --> app/bad.py:1:6",
            "1 file would be reformatted, 3 files already formatted",
        ])
        findings = LintTool._parse_lint_output(log, "python")
        self.assertEqual([f.id for f in findings], ["lint:python:format:app/bad.py"])

    def test_unparseable_failure_still_reports(self):
        findings = LintTool._parse_lint_output("ERROR: pip no pudo instalar ruff", "python")
        self.assertEqual([f.rule for f in findings], ["LINT_FAILED"])


class TestSastPythonRules(unittest.TestCase):
    FIXTURES = Path(__file__).resolve().parent / "fixtures" / "sast_python"

    def test_rules_against_fixtures_in_container(self):
        tool = SastTool()
        if not _docker_image_available(tool.image):
            self.skipTest(f"Docker o la imagen {tool.image} no están disponibles en este equipo")
        with TemporaryDirectory() as d:
            res = tool.run(str(self.FIXTURES), d)
            self.assertNotEqual(res.status, StageStatus.ERROR, res.error_message)
            py_hits = sorted(
                (f.file, f.line) for f in res.findings
                if f.rule.split(".")[-1].startswith("python-") and not f.rule.endswith("python-mcp-unsafe-tool-exec")
            )
            expected = sorted(("insecure.py", n) for n in (12, 16, 17, 18, 19, 20, 21, 22))
            self.assertEqual(py_hits, expected)
            self.assertFalse([f for f in res.findings if f.file == "safe.py"])


if __name__ == "__main__":
    unittest.main()
