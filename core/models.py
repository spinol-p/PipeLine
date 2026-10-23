from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class Severity(str, enum.Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class StageStatus(str, enum.Enum):
    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"
    SKIPPED = "skipped"


class RunVerdict(str, enum.Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    ERROR = "error"


class Finding(BaseModel):
    id: str = Field(..., description="Identificador único y estable del hallazgo (ej. stage:rule:file:line)")
    stage: str = Field(..., description="Etapa o herramienta que detectó el hallazgo")
    severity: Severity = Field(..., description="Severidad del hallazgo")
    rule: str = Field(..., description="Regla o identificador de vulnerabilidad (ej. CVE-..., gitleaks-rule, etc.)")
    file: Optional[str] = Field(None, description="Ruta relativa del archivo afectado")
    line: Optional[int] = Field(None, description="Línea de código si aplica")
    message: str = Field(..., description="Descripción detallada del hallazgo")
    fix: Optional[str] = Field(None, description="Sugerencia de corrección o versión parcheada")
    waived: bool = Field(default=False, description="Indica si existe una excepción activa para este hallazgo")
    waiver_reason: Optional[str] = Field(None, description="Motivo de la excepción declarada")


class StageResult(BaseModel):
    stage: str = Field(..., description="Nombre de la etapa")
    status: StageStatus = Field(..., description="Resultado de la etapa: pass, fail, error, skipped")
    tool: str = Field(..., description="Herramienta ejecutada (ej. gitleaks, semgrep, trivy)")
    tool_version: Optional[str] = Field(None, description="Versión de la herramienta")
    exit_code: int = Field(default=0, description="Código de salida del proceso")
    duration_s: float = Field(default=0.0, description="Duración en segundos")
    findings: List[Finding] = Field(default_factory=list, description="Lista de hallazgos detectados")
    reports: List[str] = Field(default_factory=list, description="Rutas a reportes crudos generados (SARIF, JSON, etc.)")
    error_message: Optional[str] = Field(None, description="Mensaje de error si la herramienta no pudo ejecutarse")
    log_file: Optional[str] = Field(None, description="Ruta al log completo de la ejecución")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Metadatos adicionales (ej. cobertura, total pruebas)")


class Waiver(BaseModel):
    id: str = Field(..., description="Identificador exacto del hallazgo a exceptuar")
    reason: str = Field(..., description="Justificación técnica u operacional de la excepción")
    owner: str = Field(..., description="Responsable humano que autoriza la excepción")
    expires: str = Field(..., description="Fecha de expiración en formato AAAA-MM-DD")


class ProjectConfig(BaseModel):
    name: str = Field(..., description="Identificador único del proyecto")
    path: str = Field(..., description="Ruta absoluta o relativa al directorio del servicio")
    default_mode: str = Field(default="quick", description="Modo por defecto: quick o full")
    base_ref: Optional[str] = Field("main", description="Rama o ref base para comparaciones")
    stack: Optional[str] = Field(None, description="Stack tecnológico (node, python, go, java, etc.)")
    contracts_dir: Optional[str] = Field(None, description="Directorio de contratos OpenAPI / proto")
    openapi_file: Optional[str] = Field(None, description="Ruta relativa al archivo OpenAPI si existe")
    coverage_min: Optional[int] = Field(default=None, description="Umbral mínimo de cobertura del proyecto (solo para endurecer la política)")
    allow_skipped_stages: List[str] = Field(default_factory=list, description="Etapas permitidas para omitir sin bloquear")
    sut_port: Optional[int] = Field(None, description="Puerto del servicio en modo full")
    sut_health_path: Optional[str] = Field("/health", description="Ruta de healthcheck")


class RunSummary(BaseModel):
    run_id: str = Field(..., description="ID único de corrida (ej. 20261007-152000-a1b2c3d)")
    project_name: str = Field(..., description="Nombre del proyecto revisado")
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    mode: str = Field(default="quick", description="quick o full")
    verdict: RunVerdict = Field(..., description="Veredicto final: approved o rejected")
    exit_code: int = Field(default=0, description="0 aprobado, 1 rechazado, 2 error de ejecución")
    stages: Dict[str, StageResult] = Field(default_factory=dict)
    counts_by_severity: Dict[str, int] = Field(default_factory=dict)
    blocking_findings_count: int = Field(default=0)
    waived_findings_count: int = Field(default=0)
    report_file: str = Field(..., description="Ruta a REVISION.md generado")
    summary_file: str = Field(..., description="Ruta a summary.json generado")
