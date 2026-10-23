from __future__ import annotations

import importlib
import inspect
import os
from pathlib import Path
from typing import Dict, List, Optional, Type
from core.base_tool import BaseTool


class ToolRegistry:
    """Registro y cargador dinámico de herramientas del pipeline."""

    _tools: Dict[str, BaseTool] = {}
    _discovered: bool = False

    @classmethod
    def register(cls, tool_instance: BaseTool) -> None:
        cls._tools[tool_instance.name] = tool_instance

    @classmethod
    def get_tool(cls, name: str) -> Optional[BaseTool]:
        cls.discover_all()
        return cls._tools.get(name)

    @classmethod
    def list_tools(cls) -> List[BaseTool]:
        cls.discover_all()
        return list(cls._tools.values())

    @classmethod
    def list_tool_names(cls) -> List[str]:
        cls.discover_all()
        return list(cls._tools.keys())

    @classmethod
    def discover_all(cls, tools_dir: Optional[str] = None) -> None:
        """Descubre e instancia automáticamente todas las clases BaseTool en el directorio tools/."""
        if cls._discovered and not tools_dir:
            return

        if not tools_dir:
            base_dir = Path(__file__).resolve().parent.parent / "tools"
        else:
            base_dir = Path(tools_dir)

        if not base_dir.exists():
            return

        for tool_subdir in base_dir.iterdir():
            if not tool_subdir.is_dir() or tool_subdir.name.startswith("__"):
                continue

            tool_file = tool_subdir / "tool.py"
            if not tool_file.exists():
                continue

            module_name = f"tools.{tool_subdir.name}.tool"
            try:
                module = importlib.import_module(module_name)
                for _, obj in inspect.getmembers(module):
                    if (
                        inspect.isclass(obj)
                        and issubclass(obj, BaseTool)
                        and obj is not BaseTool
                        and not inspect.isabstract(obj)
                    ):
                        instance = obj()
                        cls.register(instance)
            except Exception as e:
                print(f"[Aviso] No se pudo cargar la herramienta {module_name}: {e}")

        cls._discovered = True
