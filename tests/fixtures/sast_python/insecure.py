"""Fixture de prueba: cada línea marcada con HALLAZGO debe ser detectada por las reglas Python de SAST."""

import hashlib
import pickle
import subprocess
import tempfile

import jwt
import requests
import yaml

SECRET_KEY = "cambiar-esto"  # HALLAZGO python-hardcoded-secret-key


def unsafe(payload: bytes, raw: str, token: str, cmd: str) -> None:
    pickle.loads(payload)  # HALLAZGO python-unsafe-deserialization
    yaml.load(raw)  # HALLAZGO python-unsafe-deserialization
    subprocess.check_output(cmd, shell=True)  # HALLAZGO python-subprocess-shell-true
    requests.get("https://interno", verify=False)  # HALLAZGO python-tls-verification-disabled
    jwt.decode(token, options={"verify_signature": False})  # HALLAZGO python-jwt-signature-not-verified
    hashlib.md5(payload)  # HALLAZGO python-weak-hash
    tempfile.mktemp()  # HALLAZGO python-insecure-tempfile
