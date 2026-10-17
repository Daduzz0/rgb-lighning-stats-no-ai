"""Read measured payment events from the instrumented node's Docker stdout."""
import json
import re
import subprocess
import time

MARKER = "RGB_STATS_EVENT "
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def events_from_logs(output, payment_hash):
    events = []
    for line in output.splitlines():
        line = ANSI.sub("", line)
        if MARKER not in line:
            failure = re.search(r"EVENT: Failed to send payment to payment ID [0-9a-f]+, payment hash ([0-9a-f]{64}): ([A-Za-z0-9_]+)", line)
            if failure and failure[1] == payment_hash:
                events.append({"event": "payment_failed", "paymentHash": payment_hash, "reason": failure[2]})
            continue
        try:
            event, _ = json.JSONDecoder().raw_decode(line.split(MARKER, 1)[1])
        except (ValueError, TypeError):
            continue
        if isinstance(event, dict) and event.get("paymentHash") == payment_hash:
            events.append(event)
    return events


def measurements(events, result, identities):
    aliases = {v["pubkey"]: alias for alias, v in identities.items()}
    succeeded, failed = [], []
    fee, failure_reason = None, None
    for event in events:
        if event.get("event") == "payment_sent":
            fee = event.get("feeMsat")
        if event.get("event") == "payment_failed":
            failure_reason = event.get("reason")
        if event.get("event") not in ("path_succeeded", "path_failed"):
            continue
        hops = event.get("hops", [])
        path = {"nodes": [result["sender"]] + [aliases.get(h["pubkey"], h["pubkey"]) for h in hops],
                "hops": hops, "hopCount": len(hops), "feeMsat": event.get("feeMsat"),
                "amountMsat": event.get("amountMsat"), "blinded": event.get("blinded", False)}
        (succeeded if event["event"] == "path_succeeded" else failed).append(path)
    # LDK can replay events after a crash. Require the settled parts to reconcile with the invoice.
    delivered = sum(p["amountMsat"] for p in succeeded) if succeeded else None
    overpaid = max(0, delivered - int(result["amountMsat"])) if delivered is not None else None
    complete = (result["status"] == "succeeded" and isinstance(fee, int) and not isinstance(fee, bool)
                and fee >= 0 and bool(succeeded) and all(not p["blinded"] for p in succeeded)
                and delivered >= int(result["amountMsat"])
                and all(isinstance(p["feeMsat"], int) and p["feeMsat"] >= 0 for p in succeeded)
                and sum(p["feeMsat"] for p in succeeded) + overpaid == fee)
    return {"feeMsat": fee, "paths": succeeded or None, "failedPaths": failed,
            "pathCount": len(succeeded) if succeeded else None,
            "hopCount": sum(p["hopCount"] for p in succeeded) if succeeded else None,
            "telemetryComplete": complete, "telemetrySource": "ldk_events", "nodeEvents": events,
            "failureReason": failure_reason, "deliveredAmountMsat": delivered, "overpaymentMsat": overpaid}


def collect(result, config, identities):
    options = config.get("telemetry", {})
    if not options:
        return {}
    project, alias = options.get("project", "rgb-stats"), result["sender"]
    if options.get("provider") != "docker" or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,39}", project):
        raise ValueError("Provider telemetria non valido")
    if alias not in config["nodes"] or not re.fullmatch(r"[a-z][a-z0-9_-]*", alias):
        raise ValueError("Servizio Docker non valido")
    if not result.get("paymentHash"):
        return {}
    timeout = options.get("timeoutSec", 5)
    if isinstance(timeout, bool) or not isinstance(timeout, (float, int)) or not 0 < timeout <= 30:
        raise ValueError("Timeout telemetria non valido")
    deadline = time.monotonic() + timeout
    while True:
        response = subprocess.run(["docker", "logs", "--since", result["startedAt"],
                                   f"{project}-{alias}-1"], capture_output=True, text=True, check=True, timeout=15)
        events = events_from_logs(response.stdout + "\n" + response.stderr, result["paymentHash"])
        measured = measurements(events, result, identities)
        if measured["telemetryComplete"] or result["status"] != "succeeded" or time.monotonic() >= deadline:
            return measured
        time.sleep(0.1)
