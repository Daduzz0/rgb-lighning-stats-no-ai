import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import transaction_suite
import lab


class DockerLabTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('docker'), 'Docker CLI unavailable')
    def test_compose_has_isolated_regtest_services_and_persistent_nodes(self):
        result = subprocess.run(['docker', 'compose', '-f', str(ROOT / 'compose.yaml'), 'config', '--format', 'json'],
                                check=True, capture_output=True, text=True)
        config = json.loads(result.stdout)
        topology = json.loads((ROOT / 'topology.json').read_text())
        self.assertEqual(len(config['services']), 13)
        for node in topology['nodes']:
            service = config['services'][node['id']]
            self.assertIn('regtest', service['command'])
            self.assertIn('--disable-authentication', service['command'])
            self.assertEqual(service['ports'][0]['host_ip'], '127.0.0.1')
            self.assertEqual(int(service['ports'][0]['published']), node['apiPort'])
            self.assertEqual(service['volumes'][0]['target'], '/RLNdata')
            self.assertEqual(service['volumes'][0]['type'], 'volume')
        for service in ('bitcoind', 'electrs', 'proxy'):
            self.assertFalse(config['services'][service].get('ports'))

    def test_instrumentation_changes_only_event_handlers_and_fails_on_source_drift(self):
        spec = importlib.util.spec_from_file_location('instrument', ROOT / 'docker/instrument.py')
        instrument = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(instrument)
        source = '''prefix sendpayment RouteParametersConfig::default()
            find_and_update_rgb_chan_amt(&static_state.ldk_data_dir, &payment_hash, false);
        Event::PaymentPathSuccessful { .. } => {}
        Event::PaymentPathFailed { .. } => {}
suffix scorer route_retry'''
        changed = instrument.instrument(source)
        self.assertTrue(changed.startswith('prefix sendpayment RouteParametersConfig::default()'))
        self.assertTrue(changed.endswith('suffix scorer route_retry'))
        for event in ('payment_sent', 'path_succeeded', 'path_failed'):
            self.assertIn('"' + event + '"', changed)
        self.assertIn('path.final_value_msat()', changed)
        with self.assertRaises(ValueError):
            instrument.instrument('incompatible upstream source')

    def test_funding_id_is_not_enough_to_mine_before_broadcast(self):
        node = Mock()
        channel = {'peer_pubkey': 'peer', 'funding_txid': 'txid', 'is_usable': False, 'short_channel_id': None}
        node.request.return_value = {'channels': [channel]}
        with patch.object(lab, 'bitcoin', side_effect=subprocess.CalledProcessError(1, ['getmempoolentry'])):
            self.assertFalse(lab.funding_broadcast(node, 'peer', 'rgb-stats'))
        with patch.object(lab, 'bitcoin', return_value={'vsize': 200}) as rpc:
            self.assertTrue(lab.funding_broadcast(node, 'peer', 'rgb-stats'))
            rpc.assert_called_once_with('rgb-stats', 'getmempoolentry', 'txid')
        channel['short_channel_id'] = 123
        with patch.object(lab, 'bitcoin', side_effect=AssertionError('Already confirmed')):
            self.assertTrue(lab.funding_broadcast(node, 'peer', 'rgb-stats'))

    def test_every_original_scenario_is_explicitly_accounted_for(self):
        topology = json.loads((ROOT / 'topology.json').read_text())
        coverage = transaction_suite.reference_coverage()
        self.assertEqual({r['id'] for r in coverage}, {r['id'] for r in topology['scenarios']})
        self.assertTrue(all(r['status'] == 'reference_only' and r['reason'] for r in coverage))


if __name__ == '__main__':
    unittest.main()
