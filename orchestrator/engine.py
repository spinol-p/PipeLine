from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import yaml

from core.base_tool import BaseTool
from core.gate import GateEvaluator
from core.models import (
    Finding,
    ProjectConfig,
    RunSummary,
    RunVerdict,
    Severity,
    StageResult,
    StageStatus,
)
from core.registry import ToolRegistry
from core.reporter import RunReporter


class PipelineEngine:
    """Motor de orquestación del pipeline de revisión efímero."""

    def __init__(self, projects_file: Optional[str] = None):
        if not projects_file:
            projects_file = str(Path(__file__).resolve().parent.parent / "projects.yaml")
        self.projects_file = projects_file
        self.gate_evaluator = GateEvaluator()
        ToolRegistry.discover_all()

    def load_projects(self) -> Dict[str, ProjectConfig]:
        if not os.path.exists(self.projects_file):
            return {}
        try:
            with open(self.projects_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
                raw_projects = data.get("projects", {})
                projects = {}
                for name, p_data in raw_projects.items():
                    p_data["name"] = name
                    projects[name] = ProjectConfig(**p_data)
                return projects
        except Exception as e:
            print(f"[Aviso] Error leyendo projects.yaml: {e}")
            return {}

    def get_project(self, project_name: str) -> Optional[ProjectConfig]:
        projects = self.load_projects()
        return projects.get(project_name)

    def generate_run_id(self, project_name: str) -> str:
        now_str = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        short_hash = os.urandom(3).hex()
        return f"{now_str}-{short_hash}"

    def run_tool_scan(
        self,
        project_name: str,
        tool_name: str,
    ) -> Tuple[Optional[StageResult], str]:
        """Ejecuta una sola herramienta específica sobre un proyecto."""
        config = self.get_project(project_name)
        if not config:
            return None, f"Proyecto '{project_name}' no registrado en projects.yaml"

        tool = ToolRegistry.get_tool(tool_name)
        if not tool:
            return None, f"Herramienta '{tool_name}' no encontrada. Disponibles: {ToolRegistry.list_tool_names()}"

        run_id = self.generate_run_id(project_name)
        runs_dir = Path(__file__).resolve().parent.parent / "runs" / project_name / run_id
        runs_dir.mkdir(parents=True, exist_ok=True)

        result = tool.run(config.path, str(runs_dir), config)
        return result, str(runs_dir)

    def run_full_pipeline(
        self,
        project_name: str,
        mode: str = "quick",
        base_ref: Optional[str] = None,
    ) -> RunSummary:
        """Ejecuta todas las herramientas registradas y calcula el veredicto mediante el Gate."""
        config = self.get_project(project_name)
        if not config:
            raise ValueError(f"Proyecto '{project_name}' no registrado en projects.yaml")

        if base_ref:
            config.base_ref = base_ref

        run_id = self.generate_run_id(project_name)
        runs_dir = Path(__file__).resolve().parent.parent / "runs" / project_name / run_id
        runs_dir.mkdir(parents=True, exist_ok=True)

        stages_results: Dict[str, StageResult] = {}
        counts_by_severity: Dict[str, int] = {
            "critical": 0,
            "high": 0,
            "medium": 0,
            "low": 0,
            "info": 0,
        }

        # Ejecutar todas las herramientas registradas (D10: cada etapa aislada para que continúe la corrida)
        for tool in ToolRegistry.list_tools():
            try:
                stage_result = tool.run(config.path, str(runs_dir), config)
            except Exception as ex:
                stage_result = StageResult(
                    stage=tool.stage_name,
                    status=StageStatus.ERROR,
                    tool=tool.name,
                    exit_code=125,
                    error_message=f"Excepción no controlada ejecutando etapa '{tool.stage_name}': {str(ex)}",
                )
            stages_results[tool.stage_name] = stage_result

            for f in stage_result.findings:
                sev_key = f.severity.value
                counts_by_severity[sev_key] = counts_by_severity.get(sev_key, 0) + 1

        # Evaluar Gate
        verdict, exit_code, blocking_findings, blocking_count, waived_count = self.gate_evaluator.evaluate(
            stages_results, config
        )

        report_file = str(runs_dir / "REVISION.md")
        summary_file = str(runs_dir / "summary.json")

        summary = RunSummary(
            run_id=run_id,
            project_name=project_name,
            timestamp=datetime.now(timezone.utc).isoformat(),
            mode=mode,
            verdict=verdict,
            exit_code=exit_code,
            stages=stages_results,
            counts_by_severity=counts_by_severity,
            blocking_findings_count=blocking_count,
            waived_findings_count=waived_count,
            report_file=report_file,
            summary_file=summary_file,
        )

        # Guardar reportes siempre
        RunReporter.save_reports(summary, str(runs_dir))

        # Registrar en index.jsonl
        self._append_to_index(summary)

        return summary

    def _append_to_index(self, summary: RunSummary) -> None:
        index_file = Path(__file__).resolve().parent.parent / "runs" / "index.jsonl"
        entry = {
            "run_id": summary.run_id,
            "project": summary.project_name,
            "timestamp": summary.timestamp,
            "mode": summary.mode,
            "verdict": summary.verdict.value,
            "exit_code": summary.exit_code,
            "blocking": summary.blocking_findings_count,
            "report": summary.report_file,
        }
        with open(index_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def get_run_summary(self, project_name: str, run_id: str) -> Optional[RunSummary]:
        runs_dir = Path(__file__).resolve().parent.parent / "runs" / project_name / run_id
        summary_file = runs_dir / "summary.json"
        if not summary_file.exists():
            return None
        try:
            data = json.loads(summary_file.read_text(encoding="utf-8"))
            return RunSummary(**data)
        except Exception:
            return None

    def compare_runs(
        self, project_name: str, run_id_a: str, run_id_b: str
    ) -> Dict[str, Any]:
        """Compara dos corridas para certificar que las correcciones solucionaron los fallos sin introducir nuevas regresiones."""
        sum_a = self.get_run_summary(project_name, run_id_a)
        sum_b = self.get_run_summary(project_name, run_id_b)

        if not sum_a or not sum_b:
            return {"error": "Una o ambas corridas no fueron encontradas."}

        findings_a = {f.id: f for s in sum_a.stages.values() for f in s.findings}
        findings_b = {f.id: f for s in sum_b.stages.values() for f in s.findings}

        resolved_ids = set(findings_a.keys()) - set(findings_b.keys())
        new_ids = set(findings_b.keys()) - set(findings_a.keys())
        persistent_ids = set(findings_a.keys()) & set(findings_b.keys())

        return {
            "project": project_name,
            "baseline_run": run_id_a,
            "target_run": run_id_b,
            "baseline_verdict": sum_a.verdict.value,
            "target_verdict": sum_b.verdict.value,
            "resolved_findings_count": len(resolved_ids),
            "new_findings_count": len(new_ids),
            "persistent_findings_count": len(persistent_ids),
            "resolved_findings": [findings_a[fid].model_dump(mode="json") for fid in resolved_ids],
            "new_findings": [findings_b[fid].model_dump(mode="json") for fid in new_ids],
            "is_improved": len(new_ids) == 0 and len(resolved_ids) > 0,
        }
