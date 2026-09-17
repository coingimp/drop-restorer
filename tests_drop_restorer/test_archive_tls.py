import json
import ssl
import unittest
from unittest.mock import Mock
import requests
from urllib3.exceptions import MaxRetryError, SSLError as UrllibSSLError
from drop_restorer.core.downloader import ArchiveClient
from drop_restorer.core.models import RestorationError


class ArchiveTlsTests(unittest.TestCase):
    url = 'https://web.archive.org/web/20100618124104id_/http://www.wifi-in.cz/pridat-nove?token=secret-fixture'

    def client(self, effects):
        events, activity = [], []
        client = ArchiveClient(on_event=events.append, on_activity=activity.append)
        client.cancel = Mock()
        client.cancel.is_set.return_value = False
        client.cancel.wait.return_value = False
        client.session.get = Mock(side_effect=effects)
        client.session.close = Mock()
        return client, events, activity

    def response(self):
        response = Mock(status_code=200, is_redirect=False)
        response.headers = {'Content-Type':'text/html'}
        response.iter_content.return_value = iter([b'complete'])
        return response

    def eof(self):
        reason = ssl.SSLEOFError(8, '[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol')
        return requests.exceptions.SSLError(MaxRetryError(None, self.url, UrllibSSLError(reason)))

    def test_wrapped_eof_retries_exact_snapshot_with_certificate_verification(self):
        client, events, activity = self.client([self.eof(), self.response()])
        self.assertEqual(client.get(self.url), (b'complete','text/html'))
        self.assertEqual(client.session.get.call_count, 2)
        self.assertEqual(client.session.close.call_count, 1)
        for call in client.session.get.call_args_list:
            self.assertEqual(call.args, (self.url,))
            self.assertNotEqual(call.kwargs.get('verify'), False)
            self.assertFalse(call.kwargs['allow_redirects'])
        self.assertTrue(client.session.verify)
        self.assertIn('Повтор 2/3', events[0])
        self.assertIn('EOF', events[0])
        self.assertNotIn('secret-fixture', json.dumps([events, activity]))
        self.assertEqual(activity[-1]['status'], 'received')

    def test_certificate_and_unknown_tls_errors_fail_once(self):
        for reason in (ssl.SSLCertVerificationError(1, 'CERTIFICATE_VERIFY_FAILED'),
                       'certificate verify failed: hostname mismatch', 'WRONG_VERSION_NUMBER'):
            client, events, activity = self.client(requests.exceptions.SSLError(reason))
            with self.assertRaises(RestorationError):
                client.get(self.url)
            self.assertEqual(client.session.get.call_count, 1)
            self.assertEqual(client.session.close.call_count, 0)
            self.assertEqual(activity[-1]['status'], 'failed')

    def test_persistent_eof_is_bounded_and_logs_attempt_count_without_secrets(self):
        client, events, activity = self.client(self.eof())
        with self.assertRaises(RestorationError) as raised:
            client.get(self.url)
        self.assertEqual(client.session.get.call_count, 3)
        self.assertIn('Попыток: 3/3', str(raised.exception))
        self.assertNotIn('secret-fixture', str(raised.exception))
        self.assertEqual(activity[-1]['attempt'], 3)

    def test_partial_tls_response_is_discarded(self):
        first = self.response()
        def chunks(*args):
            yield b'incomplete'
            raise self.eof()
        first.iter_content.side_effect = chunks
        client, _, _ = self.client([first, self.response()])
        self.assertEqual(client.get(self.url)[0], b'complete')
        first.close.assert_called()

    def test_cancellation_during_tls_backoff_stops_without_next_request(self):
        client, _, _ = self.client(self.eof())
        client.cancel.wait.return_value = True
        client.cancel.is_set.side_effect = [False, False, True]
        with self.assertRaisesRegex(RestorationError, 'отменено'):
            client.get(self.url)
        self.assertEqual(client.session.get.call_count, 1)


if __name__ == '__main__':
    unittest.main()
