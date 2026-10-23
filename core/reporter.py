from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List
from core.models import Finding, RunSummary, RunVerdict, Severity, StageStatus


class RunReporter:
    """Generador de informes estructurados (summary.json) y legibles (REVISION.md)."""

    @staticmethod
    def save_reports(summary: RunSummary, run_dir: str) -> None:
        out_dir = Path(run_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        # 1. Guardar summary.json
        summary_json_path = out_dir / "summary.json"
        summary_dict = summary.model_dump(mode="json")
        with open(summary_json_path, "w", encoding="utf-8") as f:
            json.dump(summary_dict, f, indent=2, ensure_ascii=False)

        # 2. Generar REVISION.md
        md_content = RunReporter.generate_markdown(summary)
        revision_md_path = out_dir / "REVISION.md"
        with open(revision_md_path, "w", encoding="utf-8") as f:
            f.write(md_content)

    @staticmethod
    def generate_markdown(summary: RunSummary) -> str:
        verdict_str = "APROBADO" if summary.verdict == RunVerdict.APPROVED else "RECHAZADO"
        if summary.verdict == RunVerdict.ERROR:
            verdict_str = "ERROR EN EJECUCIÓN"

        md = []
        md.append(f"# Revisión — {summary.project_name}")
        md.append(
            f"**Veredicto:** {verdict_str} · **Modo:** {summary.mode} · **Fecha:** {summary.timestamp} · **Run ID:** `{summary.run_id}`\n"
        )

        # Tabla de resumen por etapa
        md.append("## Resumen de Etapas")
        md.append("| Etapa | Estado | Crítico | Alto | Medio | Bajo | Exceptuados |")
        md.append("| :--- | :--- | :---: | :---: | :---: | :---: | :---: |")

        all_findings: List[Finding] = []
        skipped_stages = []

        for stage_name, stage_res in summary.stages.items():
            if stage_res.status == StageStatus.SKIPPED:
                skipped_stages.append(stage_name)
                md.append(f"| {stage_name} | *skipped* | - | - | - | - | - |")
                continue

            crit = sum(1 for f in stage_res.findings if f.severity == Severity.CRITICAL and not f.waived)
            high = sum(1 for f in stage_res.findings if f.severity == Severity.HIGH and not f.waived)
            med = sum(1 for f in stage_res.findings if f.severity == Severity.MEDIUM and not f.waived)
            low = sum(1 for f in stage_res.findings if f.severity in (Severity.LOW, Severity.INFO) and not f.waived)
            waived = sum(1 for f in stage_res.findings if f.waived)

            status_icon = "pass" if stage_res.status == StageStatus.PASS else ("fail" if stage_res.status == StageStatus.FAIL else "error")
            all_findings.extend(stage_res.findings)
            md.append(f"| {stage_name} | **{status_icon}** | {crit} | {high} | {med} | {low} | {waived} |")

        md.append("")

        # Hallazgos bloqueantes
        blocking = [f for f in all_findings if not f.waived and f.severity in (Severity.CRITICAL, Severity.HIGH)]
        md.append("## Hallazgos que bloquean")
        if not blocking:
            md.append("*(No se encontraron hallazgos bloqueantes)*\n")
        else:
            for f in blocking:
                file_info = f" · `{f.file}:{f.line}`" if f.file else ""
                md.append(f"### 🛑 `{f.id}` · **{f.severity.value.upper()}**{file_info}")
                md.append(f"- **Regla:** {f.rule}")
                md.append(f"- **Mensaje:** {f.message}")
                if f.fix:
                    md.append(f"- **Solución sugerida:** {f.fix}")
                md.append("")

        # Hallazgos no bloqueantes
        non_blocking = [f for f in all_findings if not f.waived and f.severity not in (Severity.CRITICAL, Severity.HIGH)]
        md.append("## Hallazgos informativos y advertencias")
        if not non_blocking:
            md.append("*(Sin advertencias menores)*\n")
        else:
            for f in non_blocking:
                file_info = f" (`{f.file}:{f.line}`)" if f.file else ""
                md.append(f"- **[{f.severity.value.upper()}]** `{f.id}`{file_info}: {f.message}")
            md.append("")

        # Excepciones aplicadas
        waived_findings = [f for f in all_findings if f.waived]
        md.append("## Excepciones aplicadas (Waivers)")
        if not waived_findings:
            md.append("*(No se aplicaron excepciones en esta corrida)*\n")
        else:
            for f in waived_findings:
                md.append(f"- **`{f.id}`**: {f.waiver_reason}")
            md.append("")

        # Qué no se revisó y por qué (D15)
        md.append("## Qué no se revisó y por qué")
        unreviewed_items: List[str] = []

        # 1. Etapas omitidas (skipped)
        for s in skipped_stages:
            stage_res = summary.stages[s]
            reason = stage_res.error_message or "Declarado como omitido o no aplicable para este proyecto."
            unreviewed_items.append(f"**{s}**: {reason}")

        # 2. Etapas no ejecutadas según el modo (ej. full vs quick)
        if summary.mode == "quick":
            unreviewed_items.append(
                "**image-scan / api-conformance / smoke**: No ejecutadas en modo `quick` (requieren modo `full` con imagen construida y dependencias en ejecución)."
            )

        # 3. Cobertura de pruebas no medida
        test_stage = summary.stages.get("test")
        if test_stage and test_stage.status != StageStatus.SKIPPED:
            cov = test_stage.metadata.get("coverage_pct")
            eff_cov = test_stage.metadata.get("effective_coverage_min", 60)
            cov_src = test_stage.metadata.get("coverage_source", "policy.yaml")
            if cov is None:
                unreviewed_items.append(
                    f"**test (cobertura)**: Cobertura de código no medida (umbral exigido: {eff_cov}%, origen: {cov_src}); el proyecto no dispone de reporte estructurado (JaCoCo, SimpleCov, Istanbul o Go cover)."
                )

        # 4. Archivos parcialmente analizados en SAST
        sast_stage = summary.stages.get("sast")
        if sast_stage and sast_stage.metadata.get("partially_analyzed_files"):
            partial_count = len(sast_stage.metadata["partially_analyzed_files"])
            unreviewed_items.append(
                f"**sast (Semgrep)**: {partial_count} archivo(s) analizado(s) parcialmente debido a advertencias o sintaxis no reconocida por el motor."
            )

        # 5. Historial de secretos en monorepo
        secrets_stage = summary.stages.get("secrets")
        if secrets_stage and secrets_stage.metadata.get("scan_mode") == "dir":
            unreviewed_items.append(
                "**secrets (Gitleaks)**: Historial git no analizado; el servicio no posee directorio `.git` propio en su raíz (analizado en modo directorio sobre el árbol actual)."
            )

        # 6. Modo diferencial / base_ref (D17)
        unreviewed_items.append(
            "**diferencial (base_ref)**: Análisis diferencial no ejecutado; la corrida actual analiza el árbol de trabajo completo de forma estática."
        )

        if not unreviewed_items:
            md.append("- Todas las etapas configuradas para el modo se ejecutaron con éxito y sin exclusiones.")
        else:
            for item in unreviewed_items:
                md.append(f"- {item}")

        return "\n".join(md)
