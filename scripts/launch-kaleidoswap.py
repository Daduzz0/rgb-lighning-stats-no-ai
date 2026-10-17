#!/usr/bin/env python3
"""Launch the verified desktop app only. Connect to an existing remote node in the UI."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parent.parent
try:
    lock = json.loads((root / "release-lock.json").read_text())
    artifact = root / "runtime" / lock["artifact"]
    with artifact.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != lock["sha256"]:
            raise RuntimeError("Checksum dell'app non valido")
    print("Scegli Node e Connect to an existing remote node. Questo launcher non avvia il daemon.", flush=True)
    sys.exit(subprocess.call([str(artifact), "--appimage-extract-and-run"]))
except (OSError, ValueError, KeyError, RuntimeError) as error:
    print(f"App non disponibile: {error}. Eseguire prima scripts/download-kaleidoswap.py", file=sys.stderr)
    sys.exit(1)
