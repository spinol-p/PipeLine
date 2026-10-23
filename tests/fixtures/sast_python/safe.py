"""Fixture de prueba: equivalentes seguros que NO deben generar hallazgos de las reglas Python."""

import hashlib
import os
import subprocess
import tempfile

import jwt
import requests
import yaml

SECRET_KEY = os.environ["SECRET_KEY"]


def safe(payload: bytes, raw: str, token: str, key: str) -> None:
    yaml.safe_load(raw)
    yaml.load(raw, Loader=yaml.SafeLoader)
    subprocess.run(["ls", "-l"], check=True)
    requests.get("https://interno", timeout=5)
    jwt.decode(token, key, algorithms=["HS256"])
    hashlib.sha256(payload)
    hashlib.md5(payload, usedforsecurity=False)
    tempfile.NamedTemporaryFile()
