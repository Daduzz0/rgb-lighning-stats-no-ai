#!/usr/bin/env python3
"""Run real BTC/RGB transaction cases against the project's Docker regtest lab."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import re
from pathlib import Path
import sys

import baseline
import lab
from rln_api import ApiError
import telemetry


class ExistingInvoice:
    """Only for explicit expiry/duplicate tests; normal runner always creates invoices."""
    def __init__(self, node, invoice):
        self.node, self.invoice = node, invoice

    def request(self, endpoint, body=None, timeout=15):
        if endpoint == "lninvoice":
            return {"invoice": self.invoice}
        return self.node.request(endpoint, body, timeout)


def reference_coverage():
    """Account for every original topology scenario, including unsupported constraints."""
    topology = baseline.read(baseline.ROOT / "topology.json")
    return [{"id": scenario["id"], "status": "reference_only",
             "reason": "Fixture con vincoli di fee, esclusioni, algoritmi o estrazione AI non esposti da sendpayment upstream; nessuna equivalenza dichiarata.",
             "expectedSynthetic": scenario["expected"]} for scenario in topology["scenarios"]]


def run_suite(config, run_id, root=baseline.ROOT, faults=True):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", run_id):
        raise ValueError("run-id non valido")
    if config.get("labLayout") != "history":
        raise ValueError("La suite richiede il layout history con liquidità in entrambe le direzioni")
    if config.get("telemetry", {}).get("provider") != "docker":
        raise ValueError("La suite live richiede i nodi Docker con telemetria")
    root = Path(root)
    project = config["telemetry"]["project"]
    base = {"id": "preflight", "sender": "alice", "recipient": "judy", "amountMsat": "10000000",
            "assetAlias": "rgb:synthetic-route-test", "assetAmount": "1", "referenceOnly": {}}
    clients, identities = baseline.preflight({"requests": [base]}, config)
    required = {"alice", "bob", "judy", "heidi", "ivan"}
    if not required.issubset(clients):
        raise ValueError("Configurare collectNetworkSnapshots e tutti i nodi del lab")
    directory = root / "results" / run_id
    directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    report = {"runId": run_id, "startedAt": datetime.now(timezone.utc).isoformat(),
              "network": "regtest", "type": "live_transaction_suite", "suiteStatus": "running", "cases": [],
              "originalTopologyScenarios": reference_coverage(),
              "daemonProvenance": config.get("daemonProvenance"), "routingMode": "node_default",
              "plannedCases": 19 - (0 if faults else 4),
              "faultInjectionEnabled": faults}
    baseline.write(directory / "run.json", {**report, "nodes": identities})
    def journal(record):
        baseline.append_log(directory / "journal.jsonl", {"runId": run_id, **record})
    def snapshot():
        channels, errors = {}, {}
        for alias, client in clients.items():
            try:
                channels.update(baseline.channel_snapshot({alias: client}))
            except ApiError as error:
                errors[alias] = str(error)
        return channels, errors
    def save_report():
        report["outcomes"] = dict(Counter(c["verdict"] for c in report["cases"]))
        report["attempted"] = len(report["cases"])
        report["performance"] = baseline.performance_summary(report["cases"])
        baseline.write(directory / "suite.json", report)
    def run(case_id, asset=False, sender="alice", recipient="judy", amount=10000000,
            asset_amount=1, expected="succeeded", hop_min=None, invoice=None):
        request = {**base, "id": case_id, "sender": sender, "recipient": recipient,
                   "amountMsat": str(amount), "assetAlias": base["assetAlias"] if asset else None,
                   "assetAmount": str(asset_amount) if asset else None}
        before, errors_before = snapshot()
        case_clients = dict(clients)
        if invoice is not None:
            case_clients[recipient] = ExistingInvoice(clients[recipient], invoice)
        result = baseline.run_payment(request, config, case_clients, identities, journal, observer=telemetry.collect)
        after, errors_after = snapshot()
        failures = []
        if result["status"] != expected:
            failures.append(f"Stato atteso {expected}, osservato {result['status']}")
        if expected == "succeeded" and result["status"] == "succeeded":
            if not result["telemetryComplete"]:
                failures.append("Telemetria fee/hop incompleta")
            if hop_min is not None and (result["hopCount"] is None or result["hopCount"] < hop_min):
                failures.append(f"Attesi almeno {hop_min} hop")
            if case_id.endswith(("-direct", "-reverse")) and "multihop" not in case_id:
                if result["hopCount"] != 1 or result["feeMsat"] != 0:
                    failures.append("Il pagamento diretto deve avere un hop e fee di routing zero")
        result.update(runId=run_id, expectedStatus=expected, assertionErrors=failures,
                      verdict="failed" if failures else "passed", channelsBefore=before, channelsAfter=after,
                      snapshotErrors={"before": errors_before, "after": errors_after})
        baseline.append_log(directory / "transactions.jsonl", result)
        baseline.append_log(root / "results/transactions.jsonl", result)
        report["cases"].append({k: result.get(k) for k in (
            "id", "status", "expectedStatus", "verdict", "assertionErrors",
            "durationMs", "feeMsat", "hopCount", "pathCount", "assetId",
            "failureReason", "telemetryComplete",
        )})
        save_report()
        print(f"{case_id}: {result['verdict']} ({result['status']}, duration={result['durationMs']}ms, fee={result['feeMsat']}, hops={result['hopCount']})", flush=True)
        if result["status"] in ("submission_unknown", "inconclusive", "pending"):
            raise RuntimeError(f"Pagamento ambiguo {case_id}: riconciliare il journal prima di altri invii")
        return result
    def restore(aliases):
        lab.compose(project, "start", *aliases, capture=False)
        credentials = baseline.read(root / "state" / project / "credentials.json")
        for alias in aliases:
            def ready():
                try:
                    lab.unlock(clients[alias], credentials[alias]["password"], alias)
                    return True
                except ApiError:
                    return False
            lab.wait_for(f"riavvio {alias}", ready)
            degree = sum(alias in (c["from"], c["to"]) for c in baseline.read(baseline.ROOT / "topology.json")["channels"])
            def channels_restored():
                channels = clients[alias].request("listchannels")["channels"]
                return len(channels) == degree and all(c["is_usable"] for c in channels)
            lab.wait_for(f"canali dopo riavvio {alias}", channels_restored)
    save_report()
    try:
        for asset in (False, True):
            prefix = "rgb" if asset else "btc"
            direct = run(f"{prefix}-direct", asset=asset, recipient="bob", hop_min=1)
            if not asset and direct["status"] == "succeeded":
                entries = [baseline.json.loads(line) for line in (directory / "journal.jsonl").read_text().splitlines()]
                invoice = next(e["invoice"] for e in entries if e["id"] == "btc-direct" and e["status"] == "submitting")
                run("btc-duplicate-invoice", recipient="bob", expected="rejected", invoice=invoice)
            run(f"{prefix}-reverse", asset=asset, sender="bob", recipient="alice", hop_min=1)
            run(f"{prefix}-multihop", asset=asset, hop_min=2)
            run(f"{prefix}-multihop-reverse", asset=asset, sender="judy", recipient="alice", hop_min=2)
        minimum = clients["alice"].request("nodeinfo")["rgb_htlc_min_msat"]
        run("rgb-minimum-carrier", asset=True, recipient="bob", amount=minimum)
        run("rgb-below-minimum-carrier", asset=True, recipient="bob", amount=minimum - 1, expected="not_submitted")
        run("btc-liquidity-exhausted", amount=10**13, expected="failed")
        run("rgb-liquidity-exhausted", asset=True, asset_amount=10001, expected="failed")
        invoice = clients["bob"].request("lninvoice", {"amt_msat": 10000000, "expiry_sec": 1})["invoice"]
        lab.wait_for("scadenza invoice di test", lambda: clients["bob"].request("invoicestatus", {"invoice": invoice})["status"] == "Expired", timeout=10)
        run("btc-expired-invoice", recipient="bob", invoice=invoice, expected="not_submitted")
        # A malformed invoice is rejected locally before sendpayment; no arbitrary string is sent.
        run("btc-malformed-invoice", recipient="bob", invoice="invalid", expected="not_submitted")
        if faults:
            for aliases, expectation, label in ((["bob"], "succeeded", "detour"), (["heidi", "ivan"], "failed", "cut-set")):
                lab.compose(project, "stop", *aliases, capture=False)
                try:
                    for asset in (False, True):
                        run(f"{'rgb' if asset else 'btc'}-unavailable-{label}", asset=asset, expected=expectation, hop_min=2 if expectation == "succeeded" else None)
                finally:
                    restore(aliases)
        report["suiteStatus"] = "failed" if any(c["verdict"] == "failed" for c in report["cases"]) else "passed"
    except BaseException:
        report["suiteStatus"] = "aborted"
        raise
    finally:
        report["finishedAt"] = datetime.now(timezone.utc).isoformat()
        save_report()
    return directory, not any(c["verdict"] == "failed" for c in report["cases"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=baseline.ROOT / "state/rgb-stats/config.json")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--no-faults", action="store_true")
    args = parser.parse_args()
    try:
        baseline.validate()
        directory, passed = run_suite(baseline.read(args.config), args.run_id, faults=not args.no_faults)
        print(f"Rapporto suite: {directory / 'suite.json'}")
        return 0 if passed else 1
    except (ApiError, ValueError, RuntimeError, OSError, KeyError, lab.subprocess.SubprocessError) as error:
        print(f"Errore suite: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
