#!/usr/bin/env bash
set -euo pipefail
wallet_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$wallet_dir/scripts/launch-kaleidoswap.py"
