from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional
from mcp.server.mcpserver import MCPServer

from core.registry import ToolRegistry
from orchestrator.engine import PipelineEngine


def create_orchestrator_server() -> MCPServer:
    engine = PipelineEngine()
    server = MCPServer("review-orchestrator")

    @server.tool()
    def list_projects() -> str:
        """Lista todos los proyectos registrados en projects.yaml disponibles para certificación."""
        projects = engine.load_projects()
        result = []
        for name, p in projects.items():
            result.append({
                "name": name,
                "path": p.path,
                "default_mode": p.default_mode,
                "base_ref": p.base_ref,
                "coverage_min": p.coverage_min,
            })
        return json.dumps(result, indent=2, ensure_ascii=False)

    @server.tool()
    def list_available_tools() -> str:
        """Lista todas las herramientas de análisis y seguridad modulares cargadas en el pipeline."""
        tools = ToolRegistry.list_tools()
        result = []
        for t in tools:
            result.append({
                "name": t.name,
                "stage_name": t.stage_name,
                "description": t.description,
                "has_skill": t.skill_path.exists(),
            })
        return json.dumps(result, indent=2, ensure_ascii=False)

    @server.tool()
    def get_tool_skill(tool_name: str) -> str:
        """
        Obtiene el Skill (guía de buenas prácticas, remediación y reglas) de una herramienta específica.
        Permite al agente LLM saber cómo diagnosticar y reparar los hallazgos de esa herramienta.
        """
        tool = ToolRegistry.get_tool(tool_name)
        if not tool:
            return f"Herramienta '{tool_name}' no encontrada. Opciones: {ToolRegistry.list_tool_names()}"
        return tool.get_skill()

    @server.tool()
    def run_tool(project_name: str, tool_name: str) -> str:
        """Ejecuta una sola herramienta de revisión (ej. 'secrets', 'sast', 'deps') sobre un proyecto."""
        res, msg = engine.run_tool_scan(project_name, tool_name)
        if not res:
            return json.dumps({"error": msg}, indent=2, ensure_ascii=False)
        return json.dumps(res.model_dump(mode="json"), indent=2, ensure_ascii=False)

    @server.tool()
    def run_certification(
        project_name: str,
        mode: str = "quick",
        base_ref: Optional[str] = "main",
    ) -> str:
        """
        Ejecuta el pipeline completo de certificación (código y seguridad) sobre un proyecto.
        Devuelve el veredicto oficial (APPROVED / REJECTED) y el desglose de hallazgos.
        """
        try:
            summary = engine.run_full_pipeline(project_name, mode=mode, base_ref=base_ref)
            output = {
                "run_id": summary.run_id,
                "project": summary.project_name,
                "verdict": summary.verdict.value,
                "exit_code": summary.exit_code,
                "blocking_findings": summary.blocking_findings_count,
                "waived_findings": summary.waived_findings_count,
                "counts_by_severity": summary.counts_by_severity,
                "report_file": summary.report_file,
                "summary_file": summary.summary_file,
            }
            return json.dumps(output, indent=2, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)}, indent=2, ensure_ascii=False)

    @server.tool()
    def get_run_summary(project_name: str, run_id: str) -> str:
        """Obtiene el resumen detallado en formato JSON (summary.json) de una corrida específica."""
        summary = engine.get_run_summary(project_name, run_id)
        if not summary:
            return json.dumps({"error": f"No se encontró la corrida {run_id} para {project_name}"})
        return json.dumps(summary.model_dump(mode="json"), indent=2, ensure_ascii=False)

    @server.tool()
    def list_findings(
        project_name: str,
        run_id: str,
        stage: Optional[str] = None,
        min_severity: Optional[str] = None,
        blocking_only: bool = True,
    ) -> str:
        """
        Lista los hallazgos de una corrida con filtros para optimizar la ventana de contexto del LLM.
        """
        summary = engine.get_run_summary(project_name, run_id)
        if not summary:
            return json.dumps({"error": f"No se encontró la corrida {run_id}"})

        findings = []
        for stage_name, s_res in summary.stages.items():
            if stage and stage_name != stage:
                continue
            for f in s_res.findings:
                if blocking_only and f.waived:
                    continue
                if blocking_only and f.severity.value not in ("critical", "high"):
                    continue
                if min_severity and f.severity.value != min_severity:
                    continue
                findings.append(f.model_dump(mode="json"))

        return json.dumps({
            "run_id": run_id,
            "project": project_name,
            "total_matched": len(findings),
            "findings": findings,
        }, indent=2, ensure_ascii=False)

    @server.tool()
    def get_report(project_name: str, run_id: str) -> str:
        """Obtiene el informe completo legible REVISION.md generado para la corrida."""
        runs_dir = Path(__file__).resolve().parent.parent / "runs" / project_name / run_id
        rep = runs_dir / "REVISION.md"
        if not rep.exists():
            return f"No se encontró el reporte en {rep}"
        return rep.read_text(encoding="utf-8")

    @server.tool()
    def compare_runs(project_name: str, run_a: str, run_b: str) -> str:
        """
        Compara dos corridas para certificar si un cambio resolvió los fallos sin introducir regresiones.
        run_a = corrida base (anterior).
        run_b = corrida objetivo (tras correcciones).
        """
        diff = engine.compare_runs(project_name, run_a, run_b)
        return json.dumps(diff, indent=2, ensure_ascii=False)

    return server


def main():
    server = create_orchestrator_server()
    asyncio.run(server.run_stdio_async())


if __name__ == "__main__":
    main()
