#!/usr/bin/env python3
"""Explicitly prepare the 20-node stock single-path benchmark; never run at import."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import lab
from baseline import read, write


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("up", "down", "status", "logs"))
    parser.add_argument("--project", default="rgb-stats-benchmark")
    parser.add_argument("--label", default="machine-a")
    args = parser.parse_args()
    lab.COMPOSE_FILE = ROOT / "benchmark/compose.yaml"
    if args.command != "up":
        lab.compose(args.project, {"down": "stop", "status": "ps", "logs": "logs"}[args.command], capture=False)
        return 0
    lock = read(ROOT / "benchmark/inputs-lock.json")
    for name in ("topology.json", "scenarios.json"):
        actual = hashlib.sha256((ROOT / "benchmark" / name).read_bytes()).hexdigest()
        if actual != lock["files"][name]:
            raise ValueError(f"Shared input changed: {name}")
    # Shared topology has zero pushes, not the legacy history layout's half balances.
    config_path = lab.setup(args.project, "topology", topology_path=ROOT / "benchmark/topology.json")
    existing = read(config_path)
    spec = read(ROOT / "benchmark/topology.json")
    if set(existing["nodes"]) != {node["id"] for node in spec["nodes"]}:
        raise ValueError("The project does not contain exactly the 20 shared nodes")
    config = {
        "schemaVersion": 1, "network": "regtest", "project": args.project, "label": args.label,
        "composeFile": str(lab.COMPOSE_FILE), "nodes": existing["nodes"],
        "assets": {"TEST": existing["assets"]["rgb:synthetic-route-test"]},
        "paymentTimeoutMs": 180000, "observationTimeoutMs": 15000, "requestTimeoutMs": 120000,
        "baseline": {"repositoryCommit": existing["daemonProvenance"]["commit"], "mpp": False, "routing": "stock"},
    }
    output = ROOT / "state" / args.project / "benchmark.json"
    write(output, config)
    print(f"Benchmark config written: {output}. No benchmark payments have been sent.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(f"Benchmark setup failed: {error}", file=sys.stderr)
        sys.exit(1)
