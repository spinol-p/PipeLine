from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import yaml

from core.models import (
    Finding,
    ProjectConfig,
    RunVerdict,
    Severity,
    StageResult,
    StageStatus,
    Waiver,
)


class GateEvaluator:
    """Motor de evaluación de políticas y excepciones para certificar una corrida."""

    def __init__(self, policy_path: Optional[str] = None):
        if not policy_path:
            policy_path = str(Path(__file__).resolve().parent.parent / "policy.yaml")
        self.policy_path = policy_path
        self.policy = self._load_policy()

    def _load_policy(self) -> dict:
        if os.path.exists(self.policy_path):
            with open(self.policy_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        return {}

    def load_waivers(self, project_path: str) -> List[Waiver]:
        """Carga las excepciones declaradas en waivers.yaml dentro del repositorio del servicio."""
        waivers_file = Path(project_path) / "waivers.yaml"
        if not waivers_file.exists():
            return []

        try:
            with open(waivers_file, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or []
                waivers = []
                for item in raw:
                    if isinstance(item, dict) and "id" in item:
                        waivers.append(Waiver(**item))
                return waivers
        except Exception as e:
            print(f"[Aviso] Error leyendo waivers.yaml en {project_path}: {e}")
            return []

    def evaluate(
        self,
        stages: Dict[str, StageResult],
        project_config: ProjectConfig,
    ) -> Tuple[RunVerdict, int, List[Finding], int, int]:
        """
        Evalúa todas las etapas contra la política y excepciones.
        
        :return: (veredicto, código_salida, lista_hallazgos_bloqueantes, total_bloqueantes, total_exceptuados)
        """
        waivers = self.load_waivers(project_config.path)
        active_waivers: Dict[str, Waiver] = {}
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        for w in waivers:
            # Una excepción expirada ya no es válida
            if w.expires >= today_str:
                active_waivers[w.id] = w

        policy_stages = self.policy.get("stages", {})
        on_stage_error = self.policy.get("on_stage_error", "block")
        on_missing_stage = self.policy.get("on_missing_stage", "block")

        blocking_findings: List[Finding] = []
        total_waived = 0
        has_error = False

        # D9: Comprobar etapas faltantes o no autorizadas para omitir
        expected_stages = ["secrets", "lint", "sast", "deps", "dockerfile", "contracts", "test"]
        allow_skipped = getattr(project_config, "allow_skipped_stages", []) or []
        if on_missing_stage == "block":
            for exp_stg in expected_stages:
                if exp_stg not in stages:
                    has_error = True
                    missing_f = Finding(
                        id=f"{exp_stg}:stage_missing",
                        stage=exp_stg,
                        severity=Severity.CRITICAL,
                        rule="STAGE_MISSING",
                        message=f"La etapa esperada '{exp_stg}' no fue ejecutada o no dejó reporte",
                    )
                    blocking_findings.append(missing_f)
                    stages[exp_stg] = StageResult(
                        stage=exp_stg,
                        status=StageStatus.ERROR,
                        tool="gate",
                        findings=[missing_f],
                        error_message="Etapa requerida no ejecutada",
                    )
                elif stages[exp_stg].status == StageStatus.SKIPPED and exp_stg not in allow_skipped:
                    has_error = True
                    skip_f = Finding(
                        id=f"{exp_stg}:stage_skipped_unexpected",
                        stage=exp_stg,
                        severity=Severity.CRITICAL,
                        rule="STAGE_SKIPPED",
                        message=f"La etapa esperada '{exp_stg}' fue omitida sin autorización: {stages[exp_stg].error_message or 'sin motivo declarado'}",
                    )
                    blocking_findings.append(skip_f)
                    if not any(f.id == skip_f.id for f in stages[exp_stg].findings):
                        stages[exp_stg].findings.append(skip_f)

        for stage_name, stage_result in stages.items():
            stage_policy = policy_stages.get(stage_name, {})
            block_on = stage_policy.get("block_on", "fail")

            # 1. Comprobar si la etapa falló por error de ejecución (no pudo correr)
            if stage_result.status == StageStatus.ERROR:
                if on_stage_error == "block" and block_on != "never":
                    has_error = True
                    err_f = Finding(
                        id=f"{stage_name}:stage_execution_error",
                        stage=stage_name,
                        severity=Severity.CRITICAL,
                        rule="STAGE_ERROR",
                        message=stage_result.error_message or f"La etapa '{stage_name}' falló con código {stage_result.exit_code}",
                    )
                    blocking_findings.append(err_f)
                    if not any(f.id == err_f.id for f in stage_result.findings):
                        stage_result.findings.append(err_f)
                continue

            # 2. Comprobar hallazgos individuales de la etapa
            for finding in stage_result.findings:
                # Regla de seguridad: 'secrets' y errores de etapa no pueden tener excepciones
                can_waive = (stage_name != "secrets") and finding.rule not in ("STAGE_ERROR", "STAGE_MISSING", "STAGE_SKIPPED")
                is_waived = False

                if can_waive and finding.id in active_waivers:
                    w = active_waivers[finding.id]
                    finding.waived = True
                    finding.waiver_reason = f"Exceptuado por {w.owner} hasta {w.expires}: {w.reason}"
                    is_waived = True
                    total_waived += 1

                if is_waived:
                    continue

                # D9: only_fixable: true (solo bloquea si hay versión corregida disponible)
                if stage_policy.get("only_fixable", False) and stage_name in ("deps", "image-scan"):
                    if finding.fix is None:
                        continue

                # Determinar si este hallazgo bloquea según la política
                if self._finding_blocks(finding, block_on):
                    blocking_findings.append(finding)

            # 3. Comprobaciones de cobertura o reglas específicas
            if stage_name == "test" and stage_result.status != StageStatus.SKIPPED:
                cov = stage_result.metadata.get("coverage_pct")
                # D22: Regla de endurecimiento de política: umbral efectivo = max(policy, project)
                policy_cov = float(stage_policy.get("coverage_min", 0.0))
                proj_cov = float(project_config.coverage_min) if project_config.coverage_min is not None else 0.0
                cov_min = max(policy_cov, proj_cov)
                cov_src = "policy.yaml" if cov_min == policy_cov else "projects.yaml"
                stage_result.metadata["effective_coverage_min"] = cov_min
                stage_result.metadata["coverage_source"] = cov_src

                if cov is None:
                    cov_f = Finding(
                        id="test:coverage_not_measured",
                        stage="test",
                        severity=Severity.HIGH,
                        rule="COVERAGE_NOT_MEASURED",
                        message="Cobertura de código no medida (el proyecto no tiene herramienta de cobertura configurada o no generó reporte)",
                        fix="Configurar reporter de cobertura (ej. JaCoCo para Maven, SimpleCov para Ruby, Jest coverage para Node, pytest-cov para Python)",
                    )
                    blocking_findings.append(cov_f)
                    stage_result.findings.append(cov_f)
                elif cov < cov_min:
                    cov_f = Finding(
                        id="test:coverage_below_threshold",
                        stage="test",
                        severity=Severity.HIGH,
                        rule="COVERAGE_MIN",
                        message=f"Cobertura de código ({cov}%) es inferior al umbral mínimo exigido ({cov_min}%, origen: {cov_src})",
                        fix=f"Incrementar la cobertura de pruebas unitarias hasta alcanzar el mínimo de {cov_min}%",
                    )
                    blocking_findings.append(cov_f)
                    stage_result.findings.append(cov_f)

                if stage_policy.get("require_tests", True):
                    total_tests = stage_result.metadata.get("total_tests", 0)
                    if total_tests == 0:
                        req_f = Finding(
                            id="test:zero_tests_found",
                            stage="test",
                            severity=Severity.HIGH,
                            rule="REQUIRE_TESTS",
                            message="El proyecto no tiene pruebas declaradas o no se ejecutó ninguna prueba",
                            fix="Agregar pruebas unitarias para el servicio",
                        )
                        blocking_findings.append(req_f)
                        stage_result.findings.append(req_f)

        # Determinar veredicto final
        if has_error and len(blocking_findings) > 0 and any(f.rule in ("STAGE_ERROR", "STAGE_MISSING", "STAGE_SKIPPED") for f in blocking_findings):
            verdict = RunVerdict.ERROR
            exit_code = 2
        elif len(blocking_findings) > 0:
            verdict = RunVerdict.REJECTED
            exit_code = 1
        else:
            verdict = RunVerdict.APPROVED
            exit_code = 0

        return verdict, exit_code, blocking_findings, len(blocking_findings), total_waived

    def _finding_blocks(self, finding: Finding, block_on: str) -> bool:
        if block_on == "never":
            return False
        if block_on == "any":
            return True
        if block_on == "critical":
            return finding.severity == Severity.CRITICAL
        if block_on in ("high", "fail"):
            return finding.severity in (Severity.CRITICAL, Severity.HIGH)
        if block_on == "medium":
            return finding.severity in (Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM)
        return False
