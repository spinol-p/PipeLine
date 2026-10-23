from __future__ import annotations

import abc
import inspect
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from mcp.server.mcpserver import MCPServer
from core.models import Finding, ProjectConfig, StageResult, StageStatus


class BaseTool(abc.ABC):
    """Contrato base que toda herramienta del pipeline debe implementar."""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Nombre identificador de la herramienta (ej. 'secrets', 'sast', 'deps')."""
        pass

    @property
    @abc.abstractmethod
    def stage_name(self) -> str:
        """Nombre de la etapa que reporta en el veredicto (ej. 'secrets', 'sast')."""
        pass

    @property
    @abc.abstractmethod
    def description(self) -> str:
        """Descripción concisa de la función de la herramienta."""
        pass

    @property
    def skill_path(self) -> Path:
        """Ruta al archivo SKILL.md de la herramienta."""
        # Ruta real del módulo (no relativa al directorio actual): funciona aunque el servidor MCP se lance desde otro repo.
        module_file = Path(inspect.getfile(self.__class__)).resolve()
        # Asumiendo que la estructura es tools/<herramienta>/tool.py y tools/<herramienta>/skills/SKILL.md
        tool_dir = module_file.parent
        return tool_dir / "skills" / "SKILL.md"

    def get_skill(self) -> str:
        """Devuelve el contenido del skill operativo para los agentes LLM."""
        path = self.skill_path
        if path.exists():
            return path.read_text(encoding="utf-8")
        return f"# Skill no disponible para {self.name}\nNo se encontró {path}"

    @abc.abstractmethod
    def run(
        self,
        project_path: str,
        run_dir: str,
        config: Optional[ProjectConfig] = None,
    ) -> StageResult:
        """
        Ejecuta la revisión sobre el proyecto especificado.
        
        :param project_path: Ruta al código fuente del proyecto auditado.
        :param run_dir: Directorio de salida para esta corrida específica (donde escribir logs y SARIF).
        :param config: Opciones del proyecto (stack, contracts_dir, etc.).
        :return: StageResult normalizado con la lista de hallazgos.
        """
        pass

    @staticmethod
    def validate_execution(
        exit_code: int,
        expected_exit_codes: tuple[int, ...] | list[int],
        report_file: Optional[Path] = None,
        tool_name: str = "Herramienta",
        output_snippet: str = "",
    ) -> Optional[str]:
        """
        Valida que el código de salida y la existencia del reporte sean válidos.
        Retorna None si es conforme, o un mensaje descriptivo de error si falló.
        Principio: sin reporte legible o con código fuera de los esperados -> ERROR.
        """
        if exit_code not in expected_exit_codes:
            snippet = f": {output_snippet[:300].strip()}" if output_snippet else ""
            return f"{tool_name} finalizó con código inesperado ({exit_code}){snippet}"
        if report_file is not None and not report_file.exists():
            return f"{tool_name} terminó en código {exit_code} pero no generó el reporte esperado ({report_file.name})"
        return None

    @abc.abstractmethod

    @abc.abstractmethod
    def get_mcp_server(self) -> MCPServer:
        """
        Crea y devuelve la instancia del servidor MCP específico para esta herramienta.
        Permite que el LLM interactúe directamente con las capacidades de la herramienta.
        """
        pass
