#!/usr/bin/env python3
"""Offline preparation and explicit replay through original RLN APIs; no AI or node lifecycle."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time

from rln_api import ApiError, Rln, regtest

ROOT = Path(__file__).resolve().parent.parent
DATASETS = (20, 100, 500, 2000)
U64_MAX = (1 << 64) - 1


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def integer(value):
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value)) or not 0 < int(value) <= U64_MAX:
        raise ValueError(f"Importo intero positivo u64 non valido: {value!r}")
    return int(value)


def validate(root=ROOT):
    root = Path(root)
    manifest, topology = read(root / "manifest.json"), read(root / "topology.json")
    if manifest.get("synthetic") is not True or manifest.get("cumulative") is not True or not regtest(topology["network"]):
        raise ValueError("Richiesti dataset sintetici cumulativi e topologia regtest")
    if sha(root / "topology.json") != manifest["sourceTopologySha256"]:
        raise ValueError("Checksum topology.json non corrispondente al manifest")
    previous = []
    counts = []
    report = {"synthetic": True, "cumulative": True, "network": "regtest", "datasets": [], "livePaymentsExecuted": 0}
    nodes = {n["id"] for n in topology["nodes"]}
    for entry in manifest["datasets"]:
        path = root / entry["filename"]
        if path.parent != root or path.name != f"history-{entry['transactionCount']:04d}.json":
            raise ValueError("Nome dataset inatteso nel manifest")
        if path.stat().st_size != entry["bytes"] or sha(path) != entry["sha256"]:
            raise ValueError(f"Checksum o dimensione non valida: {path.name}")
        data = read(path)
        transactions = data["transactions"]
        if data.get("synthetic") is not True or data["provenance"]["sourceTopologySha256"] != manifest["sourceTopologySha256"]:
            raise ValueError(f"Provenienza sintetica non valida: {path.name}")
        if len(transactions) != entry["transactionCount"] or data["summary"]["transactionCount"] != len(transactions):
            raise ValueError(f"Numero transazioni non valido: {path.name}")
        if transactions[:len(previous)] != previous:
            raise ValueError("I dataset non sono prefissi cumulativi")
        ids = set()
        for tx in transactions:
            if tx["id"] in ids or tx["senderNodeId"] not in nodes or tx["recipientNodeId"] not in nodes:
                raise ValueError("Id duplicato o nodo sconosciuto")
            ids.add(tx["id"])
            integer(tx["amountMsat"])
            if tx.get("assetId"):
                integer(tx["assetAmount"])
            elif tx.get("assetAmount") is not None:
                raise ValueError("Transazione BTC con assetAmount")
        report["datasets"].append({
            "filename": path.name, "sha256": entry["sha256"], "transactions": len(transactions),
            "btc": sum(not tx.get("assetId") for tx in transactions),
            "rgb": sum(bool(tx.get("assetId")) for tx in transactions),
            "historicalMpp": sum(len(tx["paths"]) > 1 for tx in transactions),
            "referenceSyntheticOutcomes": dict(Counter(tx["outcome"] for tx in transactions)),
        })
        counts.append(len(transactions))
        previous = transactions
    if tuple(counts) != DATASETS:
        raise ValueError(f"Dataset richiesti: {DATASETS}")
    report["uniqueTransactions"] = len(previous)
    report["nodeCount"] = len(nodes)
    report["channelCount"] = len(topology["channels"])
    return report


def workload(count, root=ROOT):
    root = Path(root)
    source = root / f"history-{count:04d}.json"
    data = read(source)
    return {
        "schemaVersion": 1, "network": "regtest", "sourceDataset": source.name,
        "sourceSha256": sha(source), "sourceSynthetic": True, "routingMode": "node_default",
        "semantics": "Only endpoints and amounts are replayed. Historical paths, algorithms, constraints and outcomes are references, never instructions or measured results.",
        "initialStateReference": data["initialChannelStates"],
        "limitations": [
            "No routing field is submitted; the daemon selects routes and MPP behavior.",
            "Historical fee budgets and max_paths are not applied: the original sendpayment API does not expose these fields.",
            "No AI, custom scorer, algorithm selection, graph simulation or historical fault injection.",
            "REST alone cannot attest that a remote daemon is unmodified upstream.",
            "The runner does not start, reset, fund or rebalance nodes and channels.",
            "Actual hop paths and paid fees are null when the original REST API does not expose them.",
        ],
        "requests": [{
            "id": tx["id"], "sender": tx["senderNodeId"], "recipient": tx["recipientNodeId"],
            "amountMsat": str(integer(tx["amountMsat"])), "assetAlias": tx.get("assetId"),
            "assetAmount": str(integer(tx["assetAmount"])) if tx.get("assetId") else None,
            "referenceOnly": {"routing": tx["routing"], "outcome": tx["outcome"],
                              "feeMsat": tx["totalFeeMsat"], "durationMs": tx["durationMs"],
                              "pathCount": len(tx["paths"])},
        } for tx in data["transactions"]],
    }


def invoice_payload(request, config):
    payload = {"amt_msat": integer(request["amountMsat"]), "expiry_sec": config.get("invoiceExpirySec", 3600)}
    if request["assetAlias"]:
        asset = config.get("assets", {}).get(request["assetAlias"])
        if not isinstance(asset, str) or not asset.startswith("rgb:") or asset == request["assetAlias"]:
            raise ValueError("Associare l'asset sintetico al contract id RGB reale in config.local.json")
        payload.update(asset_id=asset, asset_amount=integer(request["assetAmount"]))
    return payload


def preflight(plan, config, factory=Rln):
    if not regtest(config.get("network")):
        raise ValueError("Sono consentiti solo nodi regtest")
    for key, default, maximum in (("requestTimeoutSec", 15, 300), ("settlementTimeoutSec", 120, 3600),
                                  ("pollIntervalSec", 1, 30), ("invoiceExpirySec", 3600, 86400)):
        value = config.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= maximum:
            raise ValueError(f"Configurazione temporale non valida: {key}")
    for request in plan["requests"]:
        invoice_payload(request, config)
    aliases = sorted({r[key] for r in plan["requests"] for key in ("sender", "recipient")})
    clients, observed, pubkeys = {}, {}, set()
    for alias in aliases:
        node = config.get("nodes", {}).get(alias, {})
        if not node.get("url"):
            raise ValueError(f"URL REST originale mancante per {alias}; nessun nodo verrà avviato")
        client = factory(node["url"], node.get("bearerTokenEnv"))
        network = client.request("networkinfo")
        info = client.request("nodeinfo")
        pubkey = info.get("pubkey", "").lower()
        if not regtest(network.get("network")) or not re.fullmatch(r"0[23][0-9a-f]{64}", pubkey):
            raise ValueError(f"Nodo {alias} non riconosciuto come daemon RLN regtest sbloccato")
        if node.get("expectedPubkey") and node["expectedPubkey"].lower() != pubkey:
            raise ValueError(f"Identità nodo non corrispondente: {alias}")
        if pubkey in pubkeys:
            raise ValueError("Due alias puntano allo stesso nodo")
        pubkeys.add(pubkey)
        clients[alias] = client
        observed[alias] = {"pubkey": pubkey, "height": network.get("height"),
                           "routingImplementation": "not_attestable_via_rest"}
    return clients, observed


def check_invoice(decoded, payload, recipient_pubkey):
    if not regtest(decoded.get("network")) or decoded.get("payee_pubkey", "").lower() != recipient_pubkey:
        raise ValueError("Invoice con rete o destinatario inattesi")
    for key in ("amt_msat", "asset_id", "asset_amount"):
        if decoded.get(key) != payload.get(key):
            raise ValueError(f"Invoice non corrispondente al workload: {key}")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", decoded.get("payment_hash", "")):
        raise ValueError("Payment hash mancante o non valido")


def run_payment(request, config, clients, identities, journal):
    sender, recipient = clients[request["sender"]], clients[request["recipient"]]
    payload = invoice_payload(request, config)
    started = time.monotonic()
    result = {"id": request["id"], "sender": request["sender"], "recipient": request["recipient"],
              "startedAt": datetime.now(timezone.utc).isoformat(),
              "routingMode": "node_default", "feeMsat": None, "paths": None,
              "referenceOnly": request["referenceOnly"], "status": "not_submitted"}
    try:
        invoice = recipient.request("lninvoice", payload)["invoice"]
        if not isinstance(invoice, str) or not invoice.startswith("lnbcrt"):
            raise ValueError("Il destinatario non ha generato una invoice regtest")
        decoded = sender.request("decodelninvoice", {"invoice": invoice})
        check_invoice(decoded, payload, identities[request["recipient"]]["pubkey"])
        result["paymentHash"] = decoded["payment_hash"]
        # Durable write BEFORE submission: an ambiguous network failure is never retried.
        journal({**result, "status": "submitting", "invoice": invoice})
        result["status"] = "submission_unknown"
        sent = sender.request("sendpayment", {"invoice": invoice}, timeout=config.get("requestTimeoutSec", 15))
        if sent.get("payment_hash") != result["paymentHash"]:
            raise ValueError("Il daemon ha restituito un payment hash inatteso")
        result["status"] = "pending"
        deadline = time.monotonic() + config.get("settlementTimeoutSec", 120)
        while time.monotonic() < deadline:
            payment = sender.request("getpayment", {"payment_hash": result["paymentHash"]})["payment"]
            if payment.get("payment_hash") != result["paymentHash"] or payment.get("inbound") is not False:
                raise ValueError("La risposta getpayment non identifica il pagamento in uscita")
            for key in ("amt_msat", "asset_id", "asset_amount"):
                if payment.get(key) != payload.get(key):
                    raise ValueError(f"Pagamento non corrispondente al workload: {key}")
            status = payment.get("status", "").lower()
            received = recipient.request("invoicestatus", {"invoice": invoice}).get("status", "").lower()
            if status == "succeeded" and received == "succeeded":
                result.update(status="succeeded", senderVerified=True, recipientVerified=True)
                break
            if status == "failed":
                if received == "succeeded":
                    raise ValueError("Stato pagamento incoerente tra mittente e destinatario")
                result["status"] = "failed"
                break
            time.sleep(config.get("pollIntervalSec", 1))
        else:
            result["status"] = "inconclusive"
    except (ApiError, ValueError, KeyError) as error:
        if result["status"] == "pending":
            result["status"] = "inconclusive"
        result["error"] = str(error)
    result["durationMs"] = round((time.monotonic() - started) * 1000)
    journal(result)
    return result


def channel_snapshot(clients):
    fields = ("channel_id", "peer_pubkey", "capacity_sat", "short_channel_id", "is_usable",
              "outbound_balance_msat", "inbound_balance_msat", "asset_id", "asset_local_amount",
              "asset_remote_amount", "balance_msat", "outbound_capacity_msat", "inbound_capacity_msat")
    return {alias: [{key: channel[key] for key in fields if key in channel}
                    for channel in client.request("listchannels")["channels"]]
            for alias, client in clients.items()}


def replay(plan, config, run_id, root=ROOT):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", run_id):
        raise ValueError("run-id deve contenere solo lettere, numeri, _ o -")
    # Read-only preflight happens before any invoice or payment is created.
    clients, identities = preflight(plan, config)
    directory = Path(root) / "results" / run_id
    directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    write(directory / "run.json", {
        "sourceSha256": plan["sourceSha256"], "sourceDataset": plan["sourceDataset"],
        "startedAt": datetime.now(timezone.utc).isoformat(), "nodes": identities,
        "limitations": plan["limitations"], "routingMode": "node_default",
    })
    write(directory / "channels-before.json", channel_snapshot(clients))
    records = []
    with (directory / "journal.jsonl").open("x") as stream:
        os.chmod(directory / "journal.jsonl", 0o600)
        def journal(record):
            stream.write(json.dumps(record) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        for request in plan["requests"]:
            result = run_payment(request, config, clients, identities, journal)
            records.append(result)
            print(f"{request['id']}: {result['status']}", flush=True)
            write(directory / "summary.json", {"attempted": len(records), "planned": len(plan["requests"]),
                  "outcomes": dict(Counter(r["status"] for r in records)), "results": records})
            if result["status"] in ("succeeded", "failed"):
                write(directory / "channels-after.json", channel_snapshot(clients))
            if result["status"] not in ("succeeded", "failed"):
                raise RuntimeError(f"Run interrotta: {result['status']}. Riconciliare il paymentHash nel journal; non ripetere l'invio.")
    return directory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "prepare", "check-nodes", "replay"))
    parser.add_argument("--dataset", type=int, choices=DATASETS, default=20)
    parser.add_argument("--config", type=Path, default=ROOT / "config.local.json")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--run-id")
    args = parser.parse_args()
    try:
        report = validate()
        if args.command == "validate":
            print(json.dumps(report, indent=2))
        elif args.command == "prepare":
            write(ROOT / "generated/validation.json", report)
            for count in DATASETS:
                write(ROOT / f"generated/workload-{count:04d}.json", workload(count))
            print(f"Preparati 4 workload cumulativi, {report['uniqueTransactions']} transazioni uniche. Nessuna connessione di rete.")
        else:
            plan = workload(args.dataset)
            if args.limit is not None:
                if args.limit < 1:
                    raise ValueError("limit deve essere positivo")
                plan["requests"] = plan["requests"][:args.limit]
            config = read(args.config)
            if args.command == "check-nodes":
                _, identities = preflight(plan, config)
                print(json.dumps(identities, indent=2))
            else:
                if not args.run_id:
                    raise ValueError("Specificare --run-id; una run esistente non viene mai sovrascritta")
                print(replay(plan, config, args.run_id))
    except (ApiError, ValueError, OSError, RuntimeError, KeyError) as error:
        print(f"Errore: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
