"""Línea de comandos del pipeline de revisión.

Uso:
    python -m orchestrator.cli list                       # proyectos registrados
    python -m orchestrator.cli tools                      # etapas disponibles
    python -m orchestrator.cli run <proyecto> [--base main]
    python -m orchestrator.cli tool <proyecto> <etapa>    # una sola etapa (secrets, lint, sast, deps, ...)
    python -m orchestrator.cli compare <proyecto> <run_a> <run_b>

Código de salida de `run`: 0 aprobado, 1 rechazado, 2 la revisión no pudo completarse.
"""

from __future__ import annotations

import argparse
import json
import sys

from core.registry import ToolRegistry
from orchestrator.engine import PipelineEngine


def _print_json(data) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="review", description="Pipeline de revisión efímero de servicios.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="Lista los proyectos de projects.yaml")
    sub.add_parser("tools", help="Lista las etapas disponibles")
    p_run = sub.add_parser("run", help="Revisión completa de un proyecto")
    p_run.add_argument("project")
    p_run.add_argument("--mode", default="quick", choices=["quick", "full"])
    p_run.add_argument("--base", default=None, help="Ref git base (por defecto la de projects.yaml)")
    p_tool = sub.add_parser("tool", help="Ejecuta una sola etapa")
    p_tool.add_argument("project")
    p_tool.add_argument("stage")
    p_cmp = sub.add_parser("compare", help="Compara dos corridas del mismo proyecto")
    p_cmp.add_argument("project")
    p_cmp.add_argument("run_a")
    p_cmp.add_argument("run_b")
    args = parser.parse_args(argv)

    engine = PipelineEngine()

    if args.cmd == "list":
        _print_json({n: p.model_dump(mode="json") for n, p in engine.load_projects().items()})
        return 0
    if args.cmd == "tools":
        _print_json([{"name": t.name, "stage": t.stage_name, "description": t.description} for t in ToolRegistry.list_tools()])
        return 0
    if args.cmd == "run":
        try:
            s = engine.run_full_pipeline(args.project, mode=args.mode, base_ref=args.base)
        except ValueError as ex:
            print(f"Error: {ex}", file=sys.stderr)
            return 2
        print(f"Veredicto: {s.verdict.value.upper()}  (bloqueantes: {s.blocking_findings_count}, exceptuados: {s.waived_findings_count})")
        for name, st in s.stages.items():
            print(f"  {name:<11} {st.status.value:<8} {len(st.findings)} hallazgo(s)")
        print(f"Informe: {s.report_file}")
        return s.exit_code
    if args.cmd == "tool":
        res, out_dir = engine.run_tool_scan(args.project, args.stage)
        if res is None:
            print(f"Error: {out_dir}", file=sys.stderr)
            return 2
        _print_json(res.model_dump(mode="json"))
        return 0 if res.status.value in ("pass", "skipped") else 1
    if args.cmd == "compare":
        _print_json(engine.compare_runs(args.project, args.run_a, args.run_b))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
