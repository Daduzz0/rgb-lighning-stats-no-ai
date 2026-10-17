#!/usr/bin/env python3
"""Download the unmodified official Linux release; never run it or start services."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / "runtime"
TAG = "v0.6.0"
FINGERPRINT = "D22687EA589C508EE1FB27259EE396C0452755F0"


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download(url, destination):
    req = urllib.request.Request(url, headers={"User-Agent": "standard-wallet-regtest"})
    with urllib.request.urlopen(req, timeout=120) as response, destination.open("wb") as out:
        while chunk := response.read(1024 * 1024):
            out.write(chunk)


def main():
    RUNTIME.mkdir(exist_ok=True)
    metadata = RUNTIME / "release.json"
    download(f"https://api.github.com/repos/kaleidoswap/desktop-app/releases/tags/{TAG}", metadata)
    release = json.loads(metadata.read_text())
    if release["tag_name"] != TAG or release["draft"] or release["prerelease"]:
        raise RuntimeError("Unexpected release metadata")
    assets = {a["name"]: a for a in release["assets"]}
    candidates = [n for n in assets if n.endswith(".AppImage") and ("amd64" in n or "x86_64" in n)]
    if len(candidates) != 1:
        raise RuntimeError(f"Expected one Linux x64 AppImage, found {candidates}")
    name = candidates[0]
    for filename in ["SHA256SUMS", "SHA256SUMS.asc"]:
        download(assets[filename]["browser_download_url"], RUNTIME / filename)
    key = RUNTIME / "release-key.asc"
    download("https://github.com/bitwalt.gpg", key)
    with tempfile.TemporaryDirectory(prefix="verify-release-", dir=RUNTIME) as directory:
        os.chmod(directory, 0o700)
        base = ["gpg", "--batch", "--homedir", directory]
        subprocess.run([*base, "--import", str(key)], check=True, capture_output=True)
        verified = subprocess.run(
            [*base, "--status-fd", "1", "--verify", str(RUNTIME / "SHA256SUMS.asc"), str(RUNTIME / "SHA256SUMS")],
            check=True, capture_output=True, text=True,
        )
        signatures = [line.split() for line in verified.stdout.splitlines() if line.startswith("[GNUPG:] VALIDSIG ")]
        if not any(parts[2] == FINGERPRINT or parts[-1] == FINGERPRINT for parts in signatures):
            raise RuntimeError("Release signature does not match the documented release key")
    checksums = {}
    for line in (RUNTIME / "SHA256SUMS").read_text().splitlines():
        match = re.match(r"^([a-fA-F0-9]{64})\s+\*?(?:\./)?(.+)$", line)
        if match:
            checksums[match[2]] = match[1].lower()
    expected = checksums[name]
    lock_path = ROOT / "release-lock.json"
    previous_lock = json.loads(lock_path.read_text()) if lock_path.exists() else {}
    if previous_lock and (previous_lock.get("tag") != TAG or previous_lock.get("artifact") != name
                          or previous_lock.get("sha256") != expected):
        raise RuntimeError("Il download non corrisponde alla release fissata nel repository")
    artifact = RUNTIME / name
    if not artifact.exists() or sha256(artifact) != expected:
        partial = artifact.with_suffix(artifact.suffix + ".partial")
        print(f"Downloading {name} ({assets[name]['size'] // (1024 * 1024)} MiB)", flush=True)
        download(assets[name]["browser_download_url"], partial)
        with partial.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != expected:
            raise RuntimeError("AppImage SHA256 mismatch; unverified partial file was not installed")
        partial.replace(artifact)
    artifact.chmod(0o755)
    lock = {**previous_lock,
        "repository": "https://github.com/kaleidoswap/desktop-app",
        "tag": TAG,
        "commit": "4372a3545b09e3a3181ee6ef80309af6140e2cba",
        "artifact": name,
        "sha256": expected,
        "signatureFingerprint": FINGERPRINT,
        "signatureVerified": True,
        "unmodifiedOfficialRelease": True,
    }
    lock_path.write_text(json.dumps(lock, indent=2) + "\n")
    print(f"Verified signed release: {artifact}", flush=True)


if __name__ == "__main__":
    main()
