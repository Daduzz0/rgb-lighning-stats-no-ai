import io
import json
from pathlib import Path
import sys
import unittest
import urllib.error
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from rln_api import ApiError, Rln, NoRedirect


class ApiClientTests(unittest.TestCase):
    def test_get_post_methods_headers_and_timeout(self):
        with patch.dict('os.environ', {'TEST_BISCUIT': 'private-token'}):
            client = Rln('http://localhost:8801/', 'TEST_BISCUIT')
        for body, method in ((None, 'GET'), ({'invoice': 'lnbcrt-example'}, 'POST')):
            with self.subTest(method=method), patch.object(client.opener, 'open') as opened:
                opened.return_value.__enter__.return_value = io.BytesIO(b'{"status":"Pending"}')
                self.assertEqual(client.request('sendpayment', body, timeout=7), {'status': 'Pending'})
                request = opened.call_args.args[0]
                self.assertEqual(request.get_method(), method)
                self.assertEqual(request.full_url, 'http://localhost:8801/sendpayment')
                self.assertEqual(request.get_header('Authorization'), 'Bearer private-token')
                self.assertEqual(opened.call_args.kwargs['timeout'], 7)
                self.assertEqual(json.loads(request.data) if request.data else None, body)

    def test_api_error_keeps_code_and_name_without_sensitive_body(self):
        client = Rln('http://localhost:8801')
        response = io.BytesIO(b'{"name":"DuplicatePayment","error":"secret-password-and-preimage"}')
        error = urllib.error.HTTPError(client.url, 403, 'Forbidden', {}, response)
        with patch.object(client.opener, 'open', side_effect=error):
            with self.assertRaises(ApiError) as caught:
                client.request('sendpayment', {'invoice': 'invoice'})
        self.assertEqual((caught.exception.http_status, caught.exception.name), (403, 'DuplicatePayment'))
        self.assertNotIn('secret', str(caught.exception))

    def test_invalid_json_and_transport_errors_are_not_retried(self):
        client = Rln('http://localhost:8801')
        for value in (b'[]', b'null', b'invalid'):
            with self.subTest(value=value), patch.object(client.opener, 'open') as opened:
                opened.return_value.__enter__.return_value = io.BytesIO(value)
                with self.assertRaises(ApiError):
                    client.request('nodeinfo')
                opened.assert_called_once()
        with patch.object(client.opener, 'open', side_effect=TimeoutError('sensitive context')) as opened:
            with self.assertRaises(ApiError) as caught:
                client.request('sendpayment', {'invoice': 'invoice'})
            opened.assert_called_once()
            self.assertNotIn('sensitive', str(caught.exception))

    def test_credentials_and_non_root_urls_are_rejected(self):
        for url in ('http://user:pass@localhost:8801', 'file:///tmp/node', 'http://localhost/api',
                    'http://localhost?token=secret', 'http://localhost/#secret', '', None):
            with self.subTest(url=url), self.assertRaises(ValueError):
                Rln(url)
        with patch.dict('os.environ', {}, clear=True), self.assertRaises(ValueError):
            Rln('http://localhost', 'MISSING_TOKEN')
        self.assertIsNone(NoRedirect().redirect_request(None, None, None, None))


if __name__ == '__main__':
    unittest.main()
