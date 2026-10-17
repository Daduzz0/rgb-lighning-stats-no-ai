"""Transaction state machine, every workload request, telemetry and durable logs."""
import concurrent.futures
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import baseline
import telemetry
from rln_api import ApiError
from test_baseline import ALICE, JUDY, HASH, ASSET, FakeNode


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.rgb = baseline.workload(20)['requests'][0]
        self.btc = next(r for r in baseline.workload(20)['requests'] if r['assetAlias'] is None)
        self.btc.update(sender='alice', recipient='judy')
        self.config = {'network': 'regtest', 'assets': {self.rgb['assetAlias']: ASSET},
                       'settlementTimeoutSec': 0.001, 'pollIntervalSec': 0.001}
        self.identities = {'alice': {'pubkey': ALICE}, 'judy': {'pubkey': JUDY}}

    def execute(self, request=None, sender_hook=None, receiver_hook=None, observer=None):
        request = copy.deepcopy(request or self.rgb)
        sender = FakeNode(ALICE, baseline.invoice_payload(request, self.config))
        receiver = FakeNode(JUDY)
        def wrap(node, hook):
            original = node.request
            def request(endpoint, body=None, timeout=15):
                response = original(endpoint, body, timeout)
                return hook(endpoint, response) if hook else response
            node.request = request
        wrap(sender, sender_hook)
        wrap(receiver, receiver_hook)
        events = []
        result = baseline.run_payment(request, self.config, {'alice': sender, 'judy': receiver},
                                      self.identities, events.append, observer=observer)
        return result, events, sender, receiver

    def test_btc_and_rgb_success_and_failure(self):
        for request in (self.btc, self.rgb):
            for status in ('Succeeded', 'Failed'):
                with self.subTest(asset=request['assetAlias'], status=status):
                    def sender_hook(endpoint, response):
                        if endpoint == 'getpayment':
                            response['payment']['status'] = status
                        return response
                    def receiver_hook(endpoint, response):
                        return {'status': 'Pending'} if endpoint == 'invoicestatus' and status == 'Failed' else response
                    result, _, sender, _ = self.execute(request, sender_hook, receiver_hook)
                    self.assertEqual(result['status'], status.lower())
                    self.assertEqual(sum(e == 'sendpayment' for e, _ in sender.calls), 1)
                    self.assertEqual(result['assetId'], ASSET if request['assetAlias'] else None)

    def test_pending_then_success_and_receiver_lag(self):
        for sender_lag, receiver_lag in ((1, 0), (0, 1), (1, 1)):
            self.config['settlementTimeoutSec'] = 1
            counters = [0, 0]
            def sender_hook(endpoint, response):
                if endpoint == 'getpayment':
                    counters[0] += 1
                    if counters[0] <= sender_lag:
                        response['payment']['status'] = 'Pending'
                return response
            def receiver_hook(endpoint, response):
                if endpoint == 'invoicestatus':
                    counters[1] += 1
                    if counters[1] <= receiver_lag:
                        return {'status': 'Pending'}
                return response
            with self.subTest(sender_lag=sender_lag, receiver_lag=receiver_lag):
                result, events, _, _ = self.execute(sender_hook=sender_hook, receiver_hook=receiver_hook)
                self.assertEqual(result['status'], 'succeeded')
                self.assertEqual(len(result['observations']), 2)
                self.assertEqual(sum(e['status'] == 'observed' for e in events), 2)

    def test_pending_timeout_never_reports_failed(self):
        def pending(endpoint, response):
            if endpoint == 'getpayment':
                response['payment']['status'] = 'Pending'
            return response
        result, _, sender, _ = self.execute(sender_hook=pending)
        self.assertEqual(result['status'], 'inconclusive')
        self.assertEqual(sum(e == 'sendpayment' for e, _ in sender.calls), 1)

    def test_invoice_mismatches_never_submit(self):
        mutations = {'network': 'Bitcoin', 'payee_pubkey': ALICE, 'amt_msat': 1,
                     'asset_id': 'rgb:other', 'asset_amount': 999, 'payment_hash': 'invalid'}
        for key, value in mutations.items():
            with self.subTest(field=key):
                def hook(endpoint, response):
                    if endpoint == 'decodelninvoice':
                        response[key] = value
                    return response
                result, events, sender, _ = self.execute(sender_hook=hook)
                self.assertEqual(result['status'], 'not_submitted')
                self.assertFalse(any(e == 'sendpayment' for e, _ in sender.calls))
                self.assertEqual(events[-1]['status'], 'not_submitted')

    def test_invalid_invoice_and_malformed_responses(self):
        for value in ('lnbc-mainnet', None, 123):
            with self.subTest(invoice=value):
                result, _, sender, _ = self.execute(receiver_hook=lambda e, r: {'invoice': value} if e == 'lninvoice' else r)
                self.assertEqual(result['status'], 'not_submitted')
                self.assertFalse(any(e == 'sendpayment' for e, _ in sender.calls))
        for value in ({}, {'payment': None}, {'payment': []}):
            result, _, _, _ = self.execute(sender_hook=lambda e, r: value if e == 'getpayment' else r)
            self.assertEqual(result['status'], 'inconclusive')

    def test_settlement_mismatches_are_inconclusive(self):
        for key, value in {'payment_hash': 'ff' * 32, 'inbound': True, 'amt_msat': 0,
                           'asset_id': 'rgb:wrong', 'asset_amount': 0, 'status': 'Unknown'}.items():
            with self.subTest(field=key):
                def hook(endpoint, response):
                    if endpoint == 'getpayment':
                        response['payment'][key] = value
                    return response
                result, _, _, _ = self.execute(sender_hook=hook)
                self.assertEqual(result['status'], 'inconclusive')

    def test_conflicting_terminal_states_are_inconclusive(self):
        def hook(endpoint, response):
            if endpoint == 'getpayment':
                response['payment']['status'] = 'Failed'
            return response
        result, _, _, _ = self.execute(sender_hook=hook)
        self.assertEqual(result['status'], 'inconclusive')
        self.assertIn('incoerente', result['error'])

    def test_network_failures_at_every_stage(self):
        for endpoint, expected in [('lninvoice', 'not_submitted'), ('decodelninvoice', 'not_submitted'),
                                   ('sendpayment', 'submission_unknown'), ('getpayment', 'inconclusive'),
                                   ('invoicestatus', 'inconclusive')]:
            def hook(e, response):
                if e == endpoint:
                    raise ApiError('network timeout')
                return response
            with self.subTest(endpoint=endpoint):
                result, events, sender, _ = self.execute(sender_hook=hook, receiver_hook=hook)
                self.assertEqual(result['status'], expected)
                self.assertLessEqual(sum(e == 'sendpayment' for e, _ in sender.calls), 1)
                self.assertEqual(events[-1]['status'], expected)

    def test_expired_invoice_is_logged_without_submission(self):
        def hook(endpoint, response):
            if endpoint == 'decodelninvoice':
                response.update(timestamp=1, expiry_sec=1)
            return response
        result, events, sender, _ = self.execute(sender_hook=hook)
        self.assertEqual(result['status'], 'not_submitted')
        self.assertIn('scaduta', result['error'])
        self.assertFalse(any(e == 'sendpayment' for e, _ in sender.calls))
        self.assertEqual(len(events), 1)

    def test_only_documented_pre_submission_api_errors_are_classified_rejected(self):
        for name, status in [('DuplicatePayment', 'rejected'), ('InvalidInvoice', 'rejected'),
                             ('Unexpected', 'submission_unknown')]:
            def hook(endpoint, response):
                if endpoint == 'sendpayment':
                    raise ApiError('API validation response', 403, name)
                return response
            with self.subTest(error=name):
                result, _, sender, _ = self.execute(sender_hook=hook)
                self.assertEqual(result['status'], status)
                self.assertEqual(sum(e == 'sendpayment' for e, _ in sender.calls), 1)

    def test_wrong_submission_hash_is_unknown(self):
        result, _, _, _ = self.execute(sender_hook=lambda e, r: {**r, 'payment_hash': 'cd' * 32} if e == 'sendpayment' else r)
        self.assertEqual(result['status'], 'submission_unknown')

    def test_invalid_amounts_and_missing_asset_are_logged(self):
        for amount in ('0', '-1', '1.2', True, str(2**64), None):
            request = {**self.rgb, 'amountMsat': amount}
            result, events, _, _ = self.execute_invalid_request(request)
            self.assertEqual(result['status'], 'not_submitted')
            self.assertEqual(len(events), 1)
        self.config['assets'] = {}
        result, _, _, _ = self.execute_invalid_request(self.rgb)
        self.assertEqual(result['status'], 'not_submitted')

    def execute_invalid_request(self, request):
        sender, receiver, events = FakeNode(ALICE, {}), FakeNode(JUDY), []
        result = baseline.run_payment(request, self.config, {'alice': sender, 'judy': receiver}, self.identities, events.append)
        self.assertEqual(sender.calls + receiver.calls, [])
        return result, events, sender, receiver

    def test_preflight_rejects_wrong_identity_duplicate_nodes_and_invalid_timeouts(self):
        plan = {'requests': [self.rgb]}
        config = {**self.config, 'nodes': {'alice': {'url': 'http://localhost:8801'},
                                          'judy': {'url': 'http://localhost:8810'}}}
        sender, receiver = FakeNode(ALICE), FakeNode(JUDY)
        def factory(url, token):
            return sender if url.endswith('8801') else receiver
        for mode in ('wrong_identity', 'duplicate_identity', 'wrong_network'):
            test_config = copy.deepcopy(config)
            receiver.pubkey = JUDY
            with self.subTest(mode=mode):
                if mode == 'wrong_identity':
                    test_config['nodes']['alice']['expectedPubkey'] = JUDY
                elif mode == 'duplicate_identity':
                    receiver.pubkey = ALICE
                else:
                    original = receiver.request
                    receiver.request = lambda e, b=None, timeout=15: {'network': 'Bitcoin'} if e == 'networkinfo' else original(e, b, timeout)
                with self.assertRaises(ValueError):
                    baseline.preflight(plan, test_config, factory=factory)
                self.assertTrue(all(e in ('networkinfo', 'nodeinfo') for e, _ in sender.calls + receiver.calls))
        for value in (0, -1, True, float('nan'), float('inf'), 301):
            with self.subTest(timeout=value), self.assertRaises(ValueError):
                baseline.preflight(plan, {**config, 'requestTimeoutSec': value}, factory=lambda *_: self.fail('Node contacted'))
        with self.assertRaises(ValueError):
            baseline.preflight(plan, {**config, 'invoiceExpirySec': 1.5}, factory=lambda *_: self.fail('Node contacted'))

    def test_payment_secrets_are_not_logged(self):
        def hook(endpoint, response):
            if endpoint == 'getpayment':
                response['payment'].update(preimage='do-not-store-preimage', secret='do-not-store-secret', payment_secret='do-not-store-payment-secret')
            return response
        result, events, _, _ = self.execute(sender_hook=hook)
        self.assertEqual(result['status'], 'succeeded')
        self.assertNotIn('do-not-store', json.dumps(events))

    def test_telemetry_failure_preserves_confirmed_settlement(self):
        def observer(*_):
            raise OSError('Docker unavailable')
        result, events, _, _ = self.execute(observer=observer)
        self.assertEqual(result['status'], 'succeeded')
        self.assertIn('Docker unavailable', result['telemetryError'])
        self.assertEqual(events[-1], result)

    def test_every_one_of_the_2000_requests_uses_exact_original_api_amounts(self):
        plan = baseline.workload(2000)
        aliases = {n['id']: {'pubkey': ('02' + f'{i + 1:064x}')} for i, n in enumerate(baseline.read(baseline.ROOT / 'topology.json')['nodes'])}
        for request in plan['requests']:
            with self.subTest(transaction=request['id']):
                payload = baseline.invoice_payload(request, self.config)
                sender, receiver = FakeNode(aliases[request['sender']]['pubkey'], payload), FakeNode(aliases[request['recipient']]['pubkey'])
                sender.decode_payee = aliases[request['recipient']]['pubkey']
                records = []
                result = baseline.run_payment(request, self.config,
                        {request['sender']: sender, request['recipient']: receiver}, aliases, records.append)
                self.assertEqual(result['status'], 'succeeded')
                self.assertEqual([b for e, b in receiver.calls if e == 'lninvoice'], [payload])
                self.assertEqual([b for e, b in sender.calls if e == 'sendpayment'], [{'invoice': 'lnbcrt-test-new-invoice'}])
                self.assertEqual(result['amountMsat'], request['amountMsat'])
                self.assertIsNone(result['feeMsat'])


class TelemetryTests(unittest.TestCase):
    def setUp(self):
        self.result = {'sender': 'alice', 'status': 'succeeded', 'amountMsat': '10000'}
        self.identities = {'alice': {'pubkey': ALICE}, 'judy': {'pubkey': JUDY}}
        self.hop = {'pubkey': JUDY, 'shortChannelId': 123, 'feeOrFinalAmountMsat': 10000, 'cltvExpiryDelta': 40}

    def test_json_logs_filter_by_hash_and_ignore_noise_and_ansi(self):
        event = {'event': 'payment_sent', 'paymentHash': HASH, 'feeMsat': 0}
        output = '\x1b[32mINFO RGB_STATS_EVENT ' + json.dumps(event) + '\x1b[0m\n'
        output += 'RGB_STATS_EVENT bad json\nRGB_STATS_EVENT []\n'
        output += 'RGB_STATS_EVENT ' + json.dumps({**event, 'paymentHash': 'ff' * 32})
        self.assertEqual(telemetry.events_from_logs(output, HASH), [event])

    def test_direct_zero_fee_and_alias_mapping(self):
        events = [{'event': 'payment_sent', 'feeMsat': 0}, {'event': 'path_succeeded', 'hops': [self.hop], 'feeMsat': 0, 'amountMsat': 10000}]
        measured = telemetry.measurements(events, self.result, self.identities)
        self.assertTrue(measured['telemetryComplete'])
        self.assertEqual(measured['feeMsat'], 0)
        self.assertEqual(measured['hopCount'], 1)
        self.assertEqual(measured['paths'][0]['nodes'], ['alice', 'judy'])

    def test_mpp_sums_hops_and_preserves_failed_attempts_without_charging_their_fee(self):
        events = [{'event': 'payment_sent', 'feeMsat': 3000},
                  {'event': 'path_succeeded', 'hops': [self.hop] * 3, 'feeMsat': 1000, 'amountMsat': 6000},
                  {'event': 'path_succeeded', 'hops': [self.hop] * 4, 'feeMsat': 2000, 'amountMsat': 4000},
                  {'event': 'path_failed', 'hops': [self.hop] * 5, 'feeMsat': 99999, 'amountMsat': 10000}]
        measured = telemetry.measurements(events, self.result, self.identities)
        self.assertTrue(measured['telemetryComplete'])
        self.assertEqual((measured['feeMsat'], measured['pathCount'], measured['hopCount']), (3000, 2, 7))
        self.assertEqual(len(measured['failedPaths']), 1)

    def test_small_overpayment_is_reconciled_with_total_paid_fee(self):
        events = [{'event': 'payment_sent', 'feeMsat': 1},
                  {'event': 'path_succeeded', 'hops': [self.hop], 'feeMsat': 0, 'amountMsat': 10001}]
        measured = telemetry.measurements(events, self.result, self.identities)
        self.assertTrue(measured['telemetryComplete'])
        self.assertEqual(measured['overpaymentMsat'], 1)
        self.assertEqual(measured['deliveredAmountMsat'], 10001)

    def test_inconsistent_paid_fee_is_reported_as_incomplete(self):
        events = [{'event': 'payment_sent', 'feeMsat': 3000},
                  {'event': 'path_succeeded', 'hops': [self.hop], 'feeMsat': 0, 'amountMsat': 10000}]
        self.assertFalse(telemetry.measurements(events, self.result, self.identities)['telemetryComplete'])

    def test_upstream_failure_reason_is_extracted_for_matching_payment_only(self):
        output = 'INFO EVENT: Failed to send payment to payment ID ' + HASH + ', payment hash ' + HASH + ': RouteNotFound'
        events = telemetry.events_from_logs(output, HASH)
        measured = telemetry.measurements(events, {**self.result, 'status': 'failed'}, self.identities)
        self.assertEqual(measured['failureReason'], 'RouteNotFound')
        self.assertIsNone(measured['feeMsat'])
        self.assertEqual(telemetry.events_from_logs(output, 'ff' * 32), [])

    def test_absent_or_duplicate_events_never_invent_complete_metrics(self):
        self.assertIsNone(telemetry.measurements([], self.result, {})['feeMsat'])
        path = {'event': 'path_succeeded', 'hops': [self.hop], 'feeMsat': 0, 'amountMsat': 10000}
        measured = telemetry.measurements([{'event': 'payment_sent', 'feeMsat': 0}, path, path], self.result, self.identities)
        self.assertFalse(measured['telemetryComplete'])


class PersistenceTests(unittest.TestCase):
    def test_concurrent_appends_are_whole_and_durable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'transactions.jsonl'
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(lambda i: baseline.append_log(path, {'id': i, 'payload': 'x' * 1000}), range(80)))
            records = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(sorted(r['id'] for r in records), list(range(80)))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_atomic_summary_preserves_previous_file_on_failed_replace(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'summary.json'
            baseline.write(path, {'attempted': 1})
            with patch.object(baseline.os, 'replace', side_effect=OSError('disk failure')):
                with self.assertRaises(OSError):
                    baseline.write(path, {'attempted': 2})
            self.assertEqual(json.loads(path.read_text()), {'attempted': 1})
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_replay_updates_global_and_run_log_after_each_transaction(self):
        request = baseline.workload(20)['requests'][0]
        config = {'network': 'regtest', 'assets': {request['assetAlias']: ASSET}}
        sender, receiver = FakeNode(ALICE, baseline.invoice_payload(request, config)), FakeNode(JUDY)
        clients = {'alice': sender, 'judy': receiver}
        identities = {'alice': {'pubkey': ALICE}, 'judy': {'pubkey': JUDY}}
        plan = {**baseline.workload(20), 'requests': [request, {**request, 'id': 'second'}]}
        original = sender.request
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def guarded(endpoint, body=None, timeout=15):
                if endpoint == 'sendpayment':
                    events = [json.loads(x) for x in (root / 'results/run/journal.jsonl').read_text().splitlines()]
                    self.assertEqual(events[-1]['status'], 'submitting')
                    self.assertEqual(events[-1]['paymentHash'], HASH)
                    if events[-1]['id'] == 'second':
                        self.assertEqual(len((root / 'results/transactions.jsonl').read_text().splitlines()), 1)
                return original(endpoint, body, timeout)
            sender.request = guarded
            with patch.object(baseline, 'preflight', return_value=(clients, identities)), contextlib.redirect_stdout(io.StringIO()):
                baseline.replay(plan, config, 'run', root=root)
                before = (root / 'results/transactions.jsonl').read_bytes()
                with self.assertRaises(FileExistsError):
                    baseline.replay(plan, config, 'run', root=root)
                self.assertEqual((root / 'results/transactions.jsonl').read_bytes(), before)
            records = [json.loads(x) for x in (root / 'results/transactions.jsonl').read_text().splitlines()]
            self.assertEqual([r['id'] for r in records], [request['id'], 'second'])
            self.assertTrue(all(r['runId'] == 'run' and 'channelsAfter' in r for r in records))
            self.assertEqual((root / 'results/run/transactions.jsonl').read_bytes(), before)
            self.assertEqual(json.loads((root / 'results/run/summary.json').read_text())['attempted'], 2)


if __name__ == '__main__':
    unittest.main()
