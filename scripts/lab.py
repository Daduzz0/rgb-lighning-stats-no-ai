#!/usr/bin/env python3
"""Manage the isolated Docker regtest lab and bootstrap its ten RLN nodes."""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time

from baseline import ROOT, channel_snapshot, read, validate, write
from rln_api import ApiError, Rln


def compose(project, *args, capture=True):
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,39}", project):
        raise ValueError("Nome progetto Docker non valido")
    return subprocess.run(["docker", "compose", "-p", project, "-f", str(ROOT / "compose.yaml"), *args],
                          check=True, text=True, capture_output=capture, timeout=1800).stdout


def bitcoin(project, *args):
    raw = compose(project, "exec", "-T", "-u", "blits", "bitcoind", "bitcoin-cli", "-regtest",
                  "-rpcuser=user", "-rpcpassword=password", *map(str, args))
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw.strip()


def wait_for(description, check, timeout=240, interval=1):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            result = check()
            if result:
                return result
        except (ApiError, OSError, subprocess.SubprocessError) as error:
            last = type(error).__name__
        time.sleep(interval)
    raise RuntimeError(f"Timeout: {description}" + (f" ({last})" if last else ""))


def mine(project, blocks=6):
    return bitcoin(project, "-rpcwallet=miner", "-generate", blocks)


def unlock(client, password, alias):
    try:
        client.request("nodeinfo")
        return
    except ApiError:
        pass
    client.request("unlock", {"password": password,
                   "ldk_chain_sync": {"mode": "BlockSync", "config": {
                       "bitcoind_rpc_username": "user", "bitcoind_rpc_password": "password",
                       "bitcoind_rpc_host": "bitcoind", "bitcoind_rpc_port": 18443}},
                   "indexer_url": "electrs:50001", "announce_addresses": [f"{alias}:9735"],
                   "announce_alias": alias}, timeout=120)


def funding_broadcast(client, peer_pubkey, project):
    channel = next((c for c in client.request("listchannels")["channels"]
                    if c["peer_pubkey"] == peer_pubkey), {})
    if channel.get("is_usable") or channel.get("short_channel_id") is not None:
        return True
    txid = channel.get("funding_txid")
    if not txid:
        return False
    try:
        return bool(bitcoin(project, "getmempoolentry", txid))
    except subprocess.CalledProcessError:
        return False


def setup(project="rgb-stats", layout="history"):
    validate()
    topology = read(ROOT / "topology.json")
    state = ROOT / "state" / project
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(state, 0o700)
    config_path, setup_path = state / "config.json", state / "setup.json"
    credentials_path = state / "credentials.json"
    if credentials_path.exists():
        credentials = read(credentials_path)
    else:
        credentials = {n["id"]: {"password": "Regtest1!" + secrets.token_urlsafe(32)} for n in topology["nodes"]}
        write(credentials_path, credentials)
    compose(project, "up", "-d", "bitcoind", capture=False)
    wait_for("Bitcoin Core", lambda: bitcoin(project, "getblockchaininfo"))
    if "miner" not in bitcoin(project, "listwallets"):
        wallets = [w["name"] for w in bitcoin(project, "listwalletdir")["wallets"]]
        bitcoin(project, "loadwallet" if "miner" in wallets else "createwallet", "miner")
    height = bitcoin(project, "getblockcount")
    if height < 103:
        mine(project, 103 - height)
    compose(project, "build", "alice", capture=False)
    compose(project, "up", "-d", "--no-build", capture=False)
    clients = {n["id"]: Rln(f"http://127.0.0.1:{n['apiPort']}") for n in topology["nodes"]}
    for alias, client in clients.items():
        # POST /init is safe to repeat only for an already initialized node: it never overwrites keys.
        def initialize():
            try:
                client.request("nodeinfo")
                return True
            except ApiError:
                pass
            try:
                response = client.request("init", {"password": credentials[alias]["password"]})
                credentials[alias].update(response)
                write(credentials_path, credentials)
                return True
            except ApiError as error:
                if error.name == "AlreadyInitialized":
                    return True
                if error.http_status is not None:
                    raise ValueError(f"Init {alias}: {error}") from error
                return False
        wait_for(f"inizializzazione {alias}", initialize)
        def unlocked():
            try:
                unlock(client, credentials[alias]["password"], alias)
                return client.request("nodeinfo")
            except ApiError as error:
                if error.name == "WrongPassword":
                    raise ValueError(f"Password non corrispondente al volume di {alias}") from error
                raise
        wait_for(f"sblocco e indexer {alias}", unlocked)
        if client.request("networkinfo")["network"].lower() != "regtest":
            raise ValueError(f"{alias}: rete diversa da regtest")
        print(f"Nodo pronto: {alias}", flush=True)
    identities = {a: c.request("nodeinfo")["pubkey"] for a, c in clients.items()}
    if setup_path.exists():
        previous = read(setup_path)
        if previous.get("status") != "ready":
            raise RuntimeError(f"Setup precedente incompleto: verificare {setup_path}. Non vengono ripetuti funding, emissione o aperture.")
        if previous["layout"] != layout or previous["identities"] != identities:
            raise ValueError("Stato Docker diverso dal setup salvato: usare un progetto nuovo")
        print(f"Lab già preparato; saldi correnti conservati. Config: {config_path}")
        return config_path
    if any(c.request("listchannels")["channels"] for c in clients.values()):
        raise ValueError("Trovati canali senza setup corrispondente; usare un progetto nuovo")
    checkpoint = {"status": "funding", "layout": layout, "identities": identities,
                  "upstream": read(ROOT / "docker/upstream-lock.json")}
    write(setup_path, checkpoint)
    print("Funding BTC e creazione UTXO RGB...", flush=True)
    addresses = {c.request("address", {})["address"]: 0.2 for c in clients.values()}
    checkpoint["fundingTxid"] = bitcoin(project, "-rpcwallet=miner", "sendmany", "", json.dumps(addresses))
    write(setup_path, checkpoint)
    mine(project)
    height = bitcoin(project, "getblockcount")
    for alias, client in clients.items():
        wait_for(f"sincronizzazione {alias}", lambda: client.request("networkinfo")["height"] >= height)
        wait_for(f"saldo BTC {alias}", lambda: client.request("btcbalance", {"skip_sync": False})["vanilla"]["spendable"] >= 20_000_000)
        client.request("createutxos", {"up_to": True, "num": 10, "size": 1_000_000,
                       "fee_rate": 2, "skip_sync": False}, timeout=120)
    mine(project)
    asset_spec = topology["assets"][0]
    checkpoint["status"] = "issuing"
    write(setup_path, checkpoint)
    print("Emissione e distribuzione asset RGB...", flush=True)
    issuer = clients[asset_spec["issuer"]]
    asset = issuer.request("issueassetnia", {"amounts": [int(asset_spec["supply"])],
                           "ticker": asset_spec["ticker"], "name": asset_spec["name"],
                           "precision": asset_spec["precision"]}, timeout=120)["asset"]["asset_id"]
    checkpoint["assetId"] = asset
    write(setup_path, checkpoint)
    recipients = []
    endpoint = "rpc://proxy:3000/json-rpc"
    for alias, amount in asset_spec["initialAllocations"].items():
        if alias == asset_spec["issuer"] or not int(amount):
            continue
        recipient_id = clients[alias].request("rgbinvoice", {"min_confirmations": 1,
                       "witness": False, "expiration_timestamp": int(time.time()) + 3600,
                       "transport_endpoints": [endpoint]})["recipient_id"]
        recipients.append({"recipient_id": recipient_id, "assignment": {"type": "Fungible", "value": int(amount)},
                           "transport_endpoints": [endpoint]})
    checkpoint["distributionTxid"] = issuer.request("sendrgb", {"donation": True, "fee_rate": 2,
                       "min_confirmations": 1, "expiration_timestamp": int(time.time()) + 3600,
                       "recipient_map": {asset: recipients}}, timeout=120)["txid"]
    write(setup_path, checkpoint)
    mine(project)
    for alias, expected in asset_spec["initialAllocations"].items():
        if not int(expected):
            continue
        client = clients[alias]
        def settled():
            client.request("refreshtransfers", {"filter": [], "skip_sync": False}, timeout=120)
            try:
                return client.request("assetbalance", {"asset_id": asset})["spendable"] == int(expected)
            except ApiError as error:
                if error.name == "AssetNotFound":
                    return False
                raise
        wait_for(f"asset spendibile {alias}", settled)
    checkpoint["status"], checkpoint["channels"] = "opening", {}
    write(setup_path, checkpoint)
    historical = read(ROOT / "history-0020.json")
    history_channels = {c["id"]: c for c in historical["topology"]["channels"]}
    balances = {c["channelId"]: c for c in historical["initialChannelStates"]}
    for channel in topology["channels"]:
        alias, peer = channel["from"], channel["to"]
        payload = {**topology["channelDefaults"], **channel}
        for key in ("id", "from", "to", "asset"):
            payload.pop(key, None)
        payload["asset_id"] = asset
        payload["peer_pubkey_and_opt_addr"] = f"{identities[peer]}@{peer}:9735"
        if layout == "history":
            initial = balances[history_channels[channel["id"]]["channelId"]]
            payload.update(push_msat=int(initial["toMsat"]), push_asset_amount=int(initial["toAssetAmount"]))
        opened = clients[alias].request("openchannel", payload, timeout=120)
        checkpoint["channels"][channel["id"]] = {"from": alias, "to": peer, **opened, "request": payload}
        write(setup_path, checkpoint)
        # Sequential openings avoid spending unconfirmed change and concurrent RGB operations.
        wait_for(f"funding {channel['id']}", lambda: funding_broadcast(clients[alias], identities[peer], project))
        mine(project)
        def usable():
            return any(c["is_usable"] for c in clients[alias].request("listchannels")["channels"]
                       if c["peer_pubkey"] == identities[peer])
        wait_for(f"canale {channel['id']}", usable)
        print(f"Canale pronto: {channel['id']}", flush=True)
    degree = Counter(c[key] for c in topology["channels"] for key in ("from", "to"))
    for alias, client in clients.items():
        wait_for(f"canali e gossip {alias}", lambda: client.request("nodeinfo")["num_usable_channels"] == degree[alias]
                 and client.request("nodeinfo")["network_channels"] >= len(topology["channels"]), timeout=300)
    checkpoint.update(status="ready", observedChannels=channel_snapshot(clients),
                      balanceNote="BTC balances include real commitment fees and reserves; reverse fee policy uses upstream defaults.")
    config = {**read(ROOT / "config.example.json"), "nodes": {a: {"url": c.url, "expectedPubkey": identities[a],
                   "bearerTokenEnv": None} for a, c in clients.items()},
              "assets": {"rgb:synthetic-route-test": asset}, "collectNetworkSnapshots": True,
              "telemetry": {"provider": "docker", "project": project, "timeoutSec": 5},
              "daemonProvenance": checkpoint["upstream"], "labLayout": layout}
    write(config_path, config)
    write(setup_path, checkpoint)
    print(f"Lab pronto: 10 nodi, 15 canali. Config: {config_path}", flush=True)
    return config_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("up", "down", "status", "logs", "mine"))
    parser.add_argument("--project", default="rgb-stats")
    parser.add_argument("--layout", choices=("history", "topology"), default="history")
    parser.add_argument("--blocks", type=int, default=6)
    args = parser.parse_args()
    try:
        if args.command == "up":
            setup(args.project, args.layout)
        elif args.command == "mine":
            if not 1 <= args.blocks <= 10000:
                raise ValueError("Numero blocchi non valido")
            mine(args.project, args.blocks)
        else:
            compose(args.project, {"down": "stop", "status": "ps", "logs": "logs"}[args.command], capture=False)
    except (ApiError, ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Errore lab: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
