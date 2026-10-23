import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from core.base_tool import BaseTool
from core.models import Severity, StageStatus
from tools.contracts.tool import ContractsTool
from tools.deps.tool import DepsTool
from tools.dockerfile.tool import DockerfileTool
from tools.sast.tool import SastTool
from tools.secrets.tool import SecretsTool


class TestParsersAndValidation(unittest.TestCase):

    def test_base_tool_validate_execution_unexpected_exit_code(self):
        err = BaseTool.validate_execution(
            exit_code=127,
            expected_exit_codes=(0, 1),
            tool_name="TestTool",
            output_snippet="command not found",
        )
        self.assertIsNotNone(err)
        self.assertIn("TestTool finalizó con código inesperado (127)", err)
        self.assertIn("command not found", err)

    def test_base_tool_validate_execution_missing_report(self):
        with TemporaryDirectory() as tmpdir:
            missing_file = Path(tmpdir) / "report.json"
            err = BaseTool.validate_execution(
                exit_code=0,
                expected_exit_codes=(0, 1),
                report_file=missing_file,
                tool_name="TestTool",
            )
            self.assertIsNotNone(err)
            self.assertIn("no generó el reporte esperado (report.json)", err)

    def test_base_tool_validate_execution_success(self):
        with TemporaryDirectory() as tmpdir:
            report_file = Path(tmpdir) / "report.json"
            report_file.write_text("{}", encoding="utf-8")
            err = BaseTool.validate_execution(
                exit_code=0,
                expected_exit_codes=(0, 1),
                report_file=report_file,
                tool_name="TestTool",
            )
            self.assertIsNone(err)

    def test_contracts_parse_real_or_mock(self):
        tool = ContractsTool()
        with TemporaryDirectory() as tmpdir:
            report_file = Path(tmpdir) / "report.json"
            spectral_data = [
                {
                    "code": "oas3-api-servers",
                    "message": "OpenAPI `servers` must be present and non-empty array.",
                    "severity": 0,
                    "range": {"start": {"line": 0}},
                },
                {
                    "code": "operation-description",
                    "message": "Operation must have description.",
                    "severity": 1,
                    "range": {"start": {"line": 15}},
                },
            ]
            report_file.write_text(json.dumps(spectral_data), encoding="utf-8")
            findings = tool._parse_json(report_file, "contracts/openapi.yaml")
            self.assertEqual(len(findings), 2)
            self.assertEqual(findings[0].severity, Severity.HIGH)
            self.assertEqual(findings[0].rule, "oas3-api-servers")
            self.assertEqual(findings[1].severity, Severity.MEDIUM)

    def test_contracts_parse_invalid_json_raises(self):
        tool = ContractsTool()
        with TemporaryDirectory() as tmpdir:
            report_file = Path(tmpdir) / "report.json"
            report_file.write_text("not json at all", encoding="utf-8")
            with self.assertRaises(Exception):
                tool._parse_json(report_file, "contracts/openapi.yaml")

    def test_deps_parse_json_valid_and_stable_id(self):
        tool = DepsTool()
        with TemporaryDirectory() as tmpdir:
            report_file = Path(tmpdir) / "report.json"
            trivy_data = {
                "Results": [
                    {
                        "Target": "Gemfile.lock",
                        "Vulnerabilities": [
                            {
                                "VulnerabilityID": "CVE-2026-47736",
                                "PkgName": "puma",
                                "InstalledVersion": "6.6.1",
                                "FixedVersion": "7.2.1",
                                "Severity": "CRITICAL",
                                "Title": "Puma Denial of Service",
                            }
                        ],
                        "Licenses": [
                            {
                                "Name": "MIT",
                                "PkgName": "puma",
                            },
                            {
                                "Name": "AGPL-3.0",
                                "PkgName": "bad-pkg",
                            },
                        ],
                    }
                ]
            }
            report_file.write_text(json.dumps(trivy_data), encoding="utf-8")
            findings, meta = tool._parse_json(report_file, denied_licenses=["AGPL-3.0"])
            # 1 CVE + 1 denied license = 2 findings (MIT license is summarized in meta, not listed as finding)
            self.assertEqual(len(findings), 2)
            cve_finding = [f for f in findings if f.rule == "CVE-2026-47736"][0]
            self.assertEqual(cve_finding.id, "deps:CVE-2026-47736:puma@6.6.1")
            self.assertEqual(cve_finding.severity, Severity.CRITICAL)
            self.assertIn("7.2.1", cve_finding.fix)

            lic_finding = [f for f in findings if "AGPL-3.0" in f.rule][0]
            self.assertEqual(lic_finding.id, "deps:license-denied:AGPL-3.0:bad-pkg")
            self.assertEqual(lic_finding.severity, Severity.HIGH)

            # Metadata contains unique licenses
            self.assertIn("MIT", meta["unique_licenses"])
            self.assertIn("AGPL-3.0", meta["unique_licenses"])

    def test_deps_parse_invalid_json_raises(self):
        tool = DepsTool()
        with TemporaryDirectory() as tmpdir:
            report_file = Path(tmpdir) / "report.json"
            report_file.write_text("malformed json", encoding="utf-8")
            with self.assertRaises(Exception):
                tool._parse_json(report_file)

    def test_contracts_run_error_on_docker_failure(self):
        tool = ContractsTool()
        with TemporaryDirectory() as tmpdir:
            from unittest.mock import patch
            from core.runner import CommandResult
            with patch("core.runner.DockerRunner.run_container") as mock_run:
                mock_run.return_value = CommandResult(exit_code=127, stdout="", stderr="command not found", duration_s=1.0)
                # Create dummy contract
                proj = Path(tmpdir) / "proj"
                proj.mkdir()
                (proj / "openapi.yaml").write_text("openapi: 3.0.0", encoding="utf-8")
                res = tool.run(str(proj), str(Path(tmpdir) / "run"))
                self.assertEqual(res.status, StageStatus.ERROR)
                self.assertIn("127", res.error_message)

    def test_deps_run_error_on_docker_failure(self):
        tool = DepsTool()
        with TemporaryDirectory() as tmpdir:
            from unittest.mock import patch
            from core.runner import CommandResult
            with patch("core.runner.DockerRunner.run_container") as mock_run:
                mock_run.return_value = CommandResult(exit_code=1, stdout="", stderr="fatal error", duration_s=1.0)
                res = tool.run(str(tmpdir), str(Path(tmpdir) / "run"))
                self.assertEqual(res.status, StageStatus.ERROR)

    def test_secrets_run_error_on_docker_failure(self):
        tool = SecretsTool()
        with TemporaryDirectory() as tmpdir:
            from unittest.mock import patch
            from core.runner import CommandResult
            with patch("core.runner.DockerRunner.run_container") as mock_run:
                mock_run.return_value = CommandResult(exit_code=2, stdout="", stderr="crash", duration_s=1.0)
                res = tool.run(str(tmpdir), str(Path(tmpdir) / "run"))
    def test_dockerfile_run_error_on_docker_failure(self):
        tool = DockerfileTool()
        with TemporaryDirectory() as tmpdir:
            from unittest.mock import patch
            from core.runner import CommandResult
            with patch("core.runner.DockerRunner.run_container") as mock_run:
                mock_run.return_value = CommandResult(exit_code=127, stdout="", stderr="hadolint: not found", duration_s=1.0)
                proj = Path(tmpdir) / "proj"
                proj.mkdir()
                (proj / "Dockerfile").write_text("FROM alpine:3.18", encoding="utf-8")
                res = tool.run(str(proj), str(Path(tmpdir) / "run"))
                self.assertEqual(res.status, StageStatus.ERROR)
                self.assertIn("127", res.error_message)

    def test_sast_run_error_on_docker_failure(self):
        tool = SastTool()
        with TemporaryDirectory() as tmpdir:
            from unittest.mock import patch
            from core.runner import CommandResult
            with patch("core.runner.DockerRunner.run_container") as mock_run:
                mock_run.return_value = CommandResult(exit_code=2, stdout="", stderr="semgrep internal error", duration_s=1.0)
                res = tool.run(str(tmpdir), str(Path(tmpdir) / "run"))
                self.assertEqual(res.status, StageStatus.ERROR)
                self.assertIn("2", res.error_message)



    def test_secrets_parse_sarif_strips_src_prefix(self):
        tool = SecretsTool()
        with TemporaryDirectory() as tmpdir:
            sarif_file = Path(tmpdir) / "report.sarif"
            sarif_data = {
                "runs": [
                    {
                        "results": [
                            {
                                "ruleId": "generic-api-key",
                                "message": {"text": "Found hardcoded API key"},
                                "locations": [
                                    {
                                        "physicalLocation": {
                                            "artifactLocation": {"uri": "/src/contracts/v1/pcs_bff.yaml"},
                                            "region": {"startLine": 329},
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
            sarif_file.write_text(json.dumps(sarif_data), encoding="utf-8")
            findings = tool._parse_sarif(sarif_file)
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].file, "contracts/v1/pcs_bff.yaml")
            self.assertEqual(findings[0].id, "secrets:generic-api-key:contracts/v1/pcs_bff.yaml:329")
            self.assertFalse(findings[0].file.startswith("/src/"))

    def test_contracts_buf_exit_code_100_parsed(self):
        tool = ContractsTool()
        with TemporaryDirectory() as tmpdir:
            from unittest.mock import patch
            from core.runner import CommandResult
            proj = Path(tmpdir) / "proj"
            proj.mkdir()
            proto_dir = proj / "contracts" / "v1"
            proto_dir.mkdir(parents=True)
            (proto_dir / "service.proto").write_text('syntax = "proto3"; package port.v1;', encoding="utf-8")

            buf_json_line = json.dumps({
                "path": "/src/contracts/v1/service.proto",
                "start_line": 16,
                "start_column": 1,
                "end_line": 16,
                "end_column": 22,
                "type": "PACKAGE_DIRECTORY_MATCH",
                "message": "Files with package \"port.v1\" must be within a directory matching their package."
            })

            with patch("core.runner.DockerRunner.run_container") as mock_run:
                # Buf exits with 100 on lint violations
                mock_run.return_value = CommandResult(exit_code=100, stdout=buf_json_line, stderr="", duration_s=1.0)
                res = tool.run(str(proj), str(Path(tmpdir) / "run"))
                self.assertEqual(res.status, StageStatus.PASS)  # Buf violations are MEDIUM, pass if no HIGH/CRIT
                buf_findings = [f for f in res.findings if "PACKAGE_DIRECTORY_MATCH" in f.rule]
                self.assertEqual(len(buf_findings), 1)
                self.assertEqual(buf_findings[0].file, "contracts/v1/service.proto")
                self.assertEqual(buf_findings[0].line, 16)
                self.assertFalse(buf_findings[0].file.startswith("/src/"))

    def test_contracts_asyncapi_invalid_spec_parsed(self):
        tool = ContractsTool()
        with TemporaryDirectory() as tmpdir:
            proj = Path(tmpdir) / "proj"
            proj.mkdir()
            contracts_dir = proj / "contracts" / "v1"
            contracts_dir.mkdir(parents=True)
            (contracts_dir / "order_events.yaml").write_text("info:\n  title: Orders", encoding="utf-8")  # missing asyncapi: version

            res = tool.run(str(proj), str(Path(tmpdir) / "run"))
            self.assertEqual(res.status, StageStatus.FAIL)
            async_f = [f for f in res.findings if f.rule == "ASYNCAPI_INVALID_SPEC"]
            self.assertEqual(len(async_f), 1)
            self.assertEqual(async_f[0].file, "contracts/v1/order_events.yaml")

    def test_lint_parse_output_with_files(self):
        from tools.lint.tool import LintTool
        tool = LintTool()

        # 1. Go unformatted files
        go_output = "=== GOFMT UNFORMATTED ===\ninternal/infrastructure/http/router.go\n"
        findings = tool._parse_lint_output(go_output, "go")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].file, "internal/infrastructure/http/router.go")
        self.assertEqual(findings[0].rule, "GOFMT_UNFORMATTED")

        # 2. Ruby RuboCop output
        ruby_output = "app/models/user.rb:25:5: C: Style/StringLiterals: Prefer single-quoted strings."
        findings = tool._parse_lint_output(ruby_output, "ruby")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].file, "app/models/user.rb")
        self.assertEqual(findings[0].line, 25)

        # 3. Java Spotless output
        java_output = "[ERROR] The following files had format violations:\n[ERROR]     src/main/java/com/port/Service.java\n"
        findings = tool._parse_lint_output(java_output, "java_springboot")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].file, "src/main/java/com/port/Service.java")

        # 4. TypeScript tsc output
        ts_output = "src/main.ts(15,3): error TS2304: Cannot find name 'ConfigService'."
        findings = tool._parse_lint_output(ts_output, "nestjs")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].file, "src/main.ts")
        self.assertEqual(findings[0].line, 15)


class TestSastNestGuardRule(unittest.TestCase):
    """Regla propia `nestjs-insecure-guard-bypass`: no debe marcar controladores con @UseGuards(...)."""

    RULE = "nestjs-insecure-guard-bypass"
    FIXTURES = Path(__file__).resolve().parent / "fixtures" / "sast"

    def _rule(self) -> str:
        rules_file = Path(__file__).resolve().parent.parent / "tools" / "sast" / "rules" / "custom-rules.yml"
        text = rules_file.read_text(encoding="utf-8")
        start = text.index(f"- id: {self.RULE}")
        nxt = text.find("\n  - id: ", start + 1)
        return text[start:] if nxt == -1 else text[start:nxt]

    def test_rule_excludes_class_and_method_guards(self):
        rule = self._rule()
        self.assertIn("pattern-inside: |\n          @Controller(...)\n          class $CLASS {", rule)
        self.assertIn("pattern-not-inside: |\n          @UseGuards(...)\n          class $CLASS {", rule)
        self.assertIn("pattern-not: |\n          @UseGuards(...)\n          $METHOD(...) { ... }", rule)
        self.assertIn("severity: WARNING", rule)

    def test_rule_against_fixtures_in_container(self):
        """Corre la herramienta `sast` real (Semgrep en Docker) contra los dos fixtures."""
        import shutil
        import subprocess
        tool = SastTool()
        available = False
        if os.environ.get("PIPELINE_SKIP_DOCKER_TESTS") != "1" and shutil.which("docker"):
            try:
                available = subprocess.run(
                    ["docker", "image", "inspect", tool.image], capture_output=True, timeout=30
                ).returncode == 0
            except Exception:
                available = False
        if not available:
            self.skipTest(f"Docker o la imagen {tool.image} no están disponibles en este equipo")

        with TemporaryDirectory() as tmpdir:
            res = tool.run(str(self.FIXTURES), tmpdir)
            self.assertEqual(res.status, StageStatus.PASS, res.error_message)
            hits = sorted((f.file, f.line) for f in res.findings if f.rule.endswith(self.RULE))
            # Los únicos hallazgos son los dos @Delete marcados como "HALLAZGO" en unguarded.controller.ts
            self.assertEqual(hits, [("unguarded.controller.ts", 14), ("unguarded.controller.ts", 31)])
            self.assertTrue(all(f.severity == Severity.MEDIUM for f in res.findings if f.rule.endswith(self.RULE)))
            self.assertFalse([f for f in res.findings if f.file == "guarded.controller.ts"])


if __name__ == "__main__":
    unittest.main()
