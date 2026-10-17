import copy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import baseline
from rln_api import ApiError, Rln

ALICE = "02" + "11" * 32
JUDY = "03" + "22" * 32
HASH = "ab" * 32
ASSET = "rgb:actual-regtest-contract-for-mock-only"


class FakeNode:
    def __init__(self, pubkey, payment=None):
        self.pubkey = pubkey
        self.calls = []
        self.payment = payment
        self.submit_error = False
        self.decode_payee = JUDY

    def request(self, endpoint, body=None, timeout=15):
        self.calls.append((endpoint, copy.deepcopy(body)))
        if endpoint == "networkinfo":
            return {"network": "Regtest", "height": 123}
        if endpoint == "nodeinfo":
            return {"pubkey": self.pubkey}
        if endpoint == "listchannels":
            return {"channels": []}
        if endpoint == "lninvoice":
            return {"invoice": "lnbcrt-test-new-invoice"}
        if endpoint == "decodelninvoice":
            return {"network": "Regtest", "payee_pubkey": self.decode_payee,
                    "payment_hash": HASH, **self.payment}
        if endpoint == "sendpayment":
            if self.submit_error:
                raise ApiError("sendpayment: TimeoutError; nessun retry automatico")
            return {"payment_hash": HASH, "status": "Pending"}
        if endpoint == "getpayment":
            return {"payment": {"payment_hash": HASH, "inbound": False,
                                "status": "Succeeded", **self.payment}}
        if endpoint == "invoicestatus":
            return {"status": "Succeeded"}
        raise AssertionError(f"Unexpected endpoint: {endpoint}")


class BaselineTests(unittest.TestCase):
    def setUp(self):
        self.request = baseline.workload(20)["requests"][0]
        self.config = {"network": "regtest", "assets": {self.request["assetAlias"]: ASSET},
                       "settlementTimeoutSec": 0.01, "pollIntervalSec": 0.001}
        self.payload = baseline.invoice_payload(self.request, self.config)
        self.sender = FakeNode(ALICE, self.payload)
        self.receiver = FakeNode(JUDY)
        self.clients = {"alice": self.sender, "judy": self.receiver}
        self.identities = {"alice": {"pubkey": ALICE}, "judy": {"pubkey": JUDY}}
        self.journal = []

    def test_original_fixtures_have_2000_unique_transactions(self):
        report = baseline.validate()
        self.assertEqual(report["uniqueTransactions"], 2000)
        self.assertEqual([d["transactions"] for d in report["datasets"]], [20, 100, 500, 2000])
        self.assertEqual((report["datasets"][-1]["btc"], report["datasets"][-1]["rgb"]), (666, 1334))
        self.assertEqual(report["livePaymentsExecuted"], 0)

    def test_tampered_fixture_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("manifest.json", "topology.json", "history-0020.json"):
                shutil.copyfile(ROOT / name, root / name)
            with (root / "history-0020.json").open("a") as stream:
                stream.write(" ")
            with self.assertRaisesRegex(ValueError, "Checksum"):
                baseline.validate(root)

    def test_preparation_never_uses_network_or_subprocesses(self):
        with patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("Network not allowed")), \
             patch("subprocess.Popen", side_effect=AssertionError("Process not allowed")):
            baseline.validate()
            plan = baseline.workload(2000)
        self.assertEqual(len(plan["requests"]), 2000)
        self.assertNotIn("routing", plan["requests"][0])
        self.assertNotIn("paths", plan["requests"][0])
        self.assertNotIn("invoice", plan["requests"][0])

    def test_baseline_submits_one_fresh_invoice_without_custom_routing(self):
        result = baseline.run_payment(self.request, self.config, self.clients, self.identities, self.journal.append)
        self.assertEqual(result["status"], "succeeded")
        self.assertTrue(result["senderVerified"] and result["recipientVerified"])
        self.assertEqual([b for e, b in self.sender.calls if e == "sendpayment"], [{"invoice": "lnbcrt-test-new-invoice"}])
        self.assertEqual([b for e, b in self.receiver.calls if e == "lninvoice"], [self.payload])
        self.assertEqual(self.journal[0]["status"], "submitting")
        self.assertEqual(self.journal[0]["paymentHash"], HASH)
        self.assertIsNone(result["feeMsat"])
        self.assertIsNone(result["paths"])

    def test_historical_failed_outcome_does_not_decide_live_result(self):
        self.request["referenceOnly"]["outcome"] = "failed"
        result = baseline.run_payment(self.request, self.config, self.clients, self.identities, self.journal.append)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["referenceOnly"]["outcome"], "failed")

    def test_ambiguous_submission_is_not_retried(self):
        self.sender.submit_error = True
        result = baseline.run_payment(self.request, self.config, self.clients, self.identities, self.journal.append)
        self.assertEqual(result["status"], "submission_unknown")
        self.assertEqual(sum(e == "sendpayment" for e, _ in self.sender.calls), 1)
        self.assertEqual(self.journal[0]["status"], "submitting")

    def test_wrong_payee_never_submits(self):
        self.sender.decode_payee = ALICE
        result = baseline.run_payment(self.request, self.config, self.clients, self.identities, self.journal.append)
        self.assertEqual(result["status"], "not_submitted")
        self.assertFalse(any(e == "sendpayment" for e, _ in self.sender.calls))

    def test_synthetic_asset_cannot_be_used_as_live_contract(self):
        self.config["assets"][self.request["assetAlias"]] = self.request["assetAlias"]
        with self.assertRaisesRegex(ValueError, "contract id"):
            baseline.invoice_payload(self.request, self.config)

    def test_mainnet_is_rejected_before_contacting_any_node(self):
        with self.assertRaisesRegex(ValueError, "regtest"):
            baseline.preflight({"requests": [self.request]}, {"network": "mainnet"}, factory=lambda *a: self.fail("Contacted a node"))

    def test_custom_service_url_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "radice REST"):
            Rln("http://127.0.0.1:8787/api")

    def test_incomplete_config_does_not_start_services(self):
        self.config["nodes"] = {"alice": {"url": None}, "judy": {"url": None}}
        with self.assertRaisesRegex(ValueError, "URL REST"):
            baseline.preflight({"requests": [self.request]}, self.config, factory=lambda *a: self.fail("Contacted a node"))

    def test_replay_stops_on_ambiguous_first_payment(self):
        self.sender.submit_error = True
        plan = baseline.workload(20)
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(baseline, "preflight", return_value=(self.clients, self.identities)):
            with self.assertRaisesRegex(RuntimeError, "submission_unknown"):
                baseline.replay(plan, self.config, "test-run", root=directory)
            summary = json.loads((Path(directory) / "results/test-run/summary.json").read_text())
            self.assertEqual(summary["attempted"], 1)
            self.assertEqual(sum(e == "sendpayment" for e, _ in self.sender.calls), 1)


if __name__ == "__main__":
    unittest.main()
