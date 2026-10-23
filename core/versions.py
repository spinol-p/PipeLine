from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional

_VERSIONS_CACHE: Dict[str, str] = {}


def load_env_versions(env_path: Optional[Path] = None) -> Dict[str, str]:
    """Carga variables de versiones desde versions.env."""
    global _VERSIONS_CACHE
    if _VERSIONS_CACHE and env_path is None:
        return _VERSIONS_CACHE

    if env_path is None:
        pipeline_root = Path(__file__).resolve().parent.parent
        env_path = pipeline_root / "versions.env"

    versions: Dict[str, str] = {}
    if env_path.exists():
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    versions[k.strip()] = v.strip()
        except Exception as e:
            print(f"[Aviso] Error leyendo versions.env: {e}")

    _VERSIONS_CACHE = versions
    return versions


def get_docker_image(env_var_name: str, fallback: str) -> str:
    """Obtiene la imagen docker configurada en versions.env o variable de entorno, con valor por defecto."""
    versions = load_env_versions()
    return os.environ.get(env_var_name, versions.get(env_var_name, fallback))
