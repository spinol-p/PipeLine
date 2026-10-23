from __future__ import annotations

import os
import subprocess
import time
from typing import Dict, List, Optional, Tuple


def normalize_docker_path(path: str) -> str:
    """Normaliza rutas de Windows (D:\\... o D:/...) a formato compatible con Docker (-v).
    Si es un volumen nombrado (ej. 'review-trivy-db' o 'review-work-xyz'), se preserva intacto.
    """
    if not (":" in path or "/" in path or "\\" in path):
        return path
    abs_path = os.path.abspath(path)
    clean_path = abs_path.replace("\\", "/")
    return clean_path


class CommandResult:
    def __init__(self, exit_code: int, stdout: str, stderr: str, duration_s: float):
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.duration_s = duration_s

    @property
    def output(self) -> str:
        return f"{self.stdout}\n{self.stderr}".strip()


class DockerRunner:
    """Ejecutor de contenedores Docker efímeros para herramientas de revisión."""

    @staticmethod
    def run_container(
        image: str,
        command: List[str] | str,
        volumes: Dict[str, str],  # host_path: container_path (con :ro o :rw opcional)
        environment: Optional[Dict[str, str]] = None,
        working_dir: Optional[str] = None,
        network: Optional[str] = None,
        entrypoint: Optional[str] = None,
        timeout_seconds: int = 600,
        name: Optional[str] = None,
    ) -> CommandResult:
        container_name = name or f"review-c-{os.urandom(4).hex()}"
        cmd = ["docker", "run", "--rm", "--name", container_name]

        if working_dir:
            cmd.extend(["-w", working_dir])

        if network:
            cmd.extend(["--network", network])

        if entrypoint is not None:
            cmd.extend(["--entrypoint", entrypoint])

        # Montar volúmenes
        for host_p, cont_p in volumes.items():
            norm_host = normalize_docker_path(host_p)
            cmd.extend(["-v", f"{norm_host}:{cont_p}"])

        # Variables de entorno
        if environment:
            for k, v in environment.items():
                cmd.extend(["-e", f"{k}={v}"])

        cmd.append(image)

        if isinstance(command, list):
            cmd.extend(command)
        elif isinstance(command, str):
            cmd.extend(["sh", "-c", command])

        start_time = time.time()
        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout_seconds,
                encoding="utf-8",
                errors="replace",
            )
            duration = time.time() - start_time
            return CommandResult(proc.returncode, proc.stdout, proc.stderr, duration)
        except subprocess.TimeoutExpired as te:
            duration = time.time() - start_time
            return CommandResult(
                124,  # Código estándar de timeout
                te.stdout or "",
                f"Error: Ejecución excedió el límite de {timeout_seconds} segundos.\n{te.stderr or ''}",
                duration,
            )
        except Exception as exc:
            duration = time.time() - start_time
            return CommandResult(125, "", f"Excepción al ejecutar contenedor: {str(exc)}", duration)
        finally:
            subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)

    @staticmethod
    def run_host_command(
        command: List[str],
        cwd: Optional[str] = None,
        timeout_seconds: int = 300,
        env: Optional[Dict[str, str]] = None,
    ) -> CommandResult:
        start_time = time.time()
        try:
            current_env = os.environ.copy()
            if env:
                current_env.update(env)
            proc = subprocess.run(
                command,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout_seconds,
                encoding="utf-8",
                errors="replace",
                env=current_env,
            )
            duration = time.time() - start_time
            return CommandResult(proc.returncode, proc.stdout, proc.stderr, duration)
        except subprocess.TimeoutExpired as te:
            duration = time.time() - start_time
            return CommandResult(124, te.stdout or "", f"Timeout de {timeout_seconds}s expirado", duration)
        except Exception as exc:
            duration = time.time() - start_time
            return CommandResult(125, "", f"Excepción ejecutando comando en host: {str(exc)}", duration)
