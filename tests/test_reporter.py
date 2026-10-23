import unittest
from core.models import Finding, RunSummary, RunVerdict, Severity, StageResult, StageStatus
from core.reporter import RunReporter


class TestReporter(unittest.TestCase):
    def test_generate_markdown_unreviewed_section(self):
        stages = {
            "secrets": StageResult(
                stage="secrets",
                status=StageStatus.PASS,
                tool="gitleaks",
                exit_code=0,
                duration_s=1.0,
                metadata={"scan_mode": "dir", "is_git_repo": False},
            ),
            "test": StageResult(
                stage="test",
                status=StageStatus.FAIL,
                tool="test_runner",
                exit_code=0,
                duration_s=2.0,
                metadata={"coverage_pct": None, "total_tests": 10},
            ),
            "dockerfile": StageResult(
                stage="dockerfile",
                status=StageStatus.SKIPPED,
                tool="hadolint",
                exit_code=0,
                duration_s=0.1,
                error_message="El servicio no define un Dockerfile",
            ),
        }
        summary = RunSummary(
            run_id="20261007-test-run",
            project_name="sample-service",
            timestamp="2026-10-07T12:00:00Z",
            mode="quick",
            verdict=RunVerdict.REJECTED,
            exit_code=1,
            stages=stages,
            counts_by_severity={},
            blocking_findings_count=1,
            waived_findings_count=0,
            report_file="REVISION.md",
            summary_file="summary.json",
        )

        md = RunReporter.generate_markdown(summary)
        self.assertIn("## Qué no se revisó y por qué", md)
        self.assertIn("**dockerfile**: El servicio no define un Dockerfile", md)
        self.assertIn("**test (cobertura)**: Cobertura de código no medida", md)
        self.assertIn("**secrets (Gitleaks)**: Historial git no analizado", md)
        self.assertIn("**image-scan / api-conformance / smoke**: No ejecutadas en modo `quick`", md)
        self.assertIn("**diferencial (base_ref)**", md)


if __name__ == "__main__":
    unittest.main()
