"""Local-only safety tests for HTTP command retry behavior."""

import sys
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cli import core_bridge


class _State:
    def current_server_base_url(self):
        return "http://127.0.0.1:14501"


class _Transport:
    class TransportError(Exception):
        pass

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def post_json(self, url_base, endpoint, payload, timeout):
        self.calls.append((url_base, endpoint, payload, timeout))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _wrapped_transport_error(cause):
    error = _Transport.TransportError(str(cause))
    error.__cause__ = cause
    return error


class PostRetrySafetyTests(unittest.TestCase):
    def test_wrapped_connection_refused_retries_once_with_same_request(self):
        error = _wrapped_transport_error(
            ConnectionRefusedError("listener unavailable")
        )
        payload = {"action": "create"}
        transport = _Transport(error, "ok")
        post = core_bridge._make_post_with_retry(transport, _State(), 10)

        with mock.patch.object(core_bridge.time, "sleep") as sleep:
            self.assertEqual("ok", post("command", payload, timeout=7))

        self.assertEqual(
            [
                ("http://127.0.0.1:14501", "command", payload, 7),
                ("http://127.0.0.1:14501", "command", payload, 7),
            ],
            transport.calls,
        )
        sleep.assert_called_once_with(core_bridge._RETRY_DELAY_S)

    def test_errno_connection_refused_retries_once(self):
        import errno

        error = OSError(errno.ECONNREFUSED, "listener unavailable")
        transport = _Transport(error, "ok")
        post = core_bridge._make_post_with_retry(transport, _State(), 10)

        with mock.patch.object(core_bridge.time, "sleep"):
            self.assertEqual("ok", post("batch", {"commands": []}))

        self.assertEqual(2, len(transport.calls))

    def test_wrapped_winsock_connection_refused_retries_once(self):
        import urllib.error

        winsock_error = OSError("listener unavailable")
        winsock_error.winerror = 10061
        error = _wrapped_transport_error(urllib.error.URLError(winsock_error))
        transport = _Transport(error, "ok")
        post = core_bridge._make_post_with_retry(transport, _State(), 10)

        with mock.patch.object(core_bridge.time, "sleep"):
            self.assertEqual("ok", post("command", {}))

        self.assertEqual(2, len(transport.calls))

    def test_timeout_does_not_repeat_post(self):
        error = _wrapped_transport_error(TimeoutError("response timed out"))
        transport = _Transport(error, "unexpected retry")
        post = core_bridge._make_post_with_retry(transport, _State(), 10)

        with mock.patch.object(core_bridge.time, "sleep") as sleep:
            with self.assertRaises(_Transport.TransportError) as raised:
                post("command", {"action": "create"})

        self.assertIs(error, raised.exception)
        self.assertEqual(1, len(transport.calls))
        sleep.assert_not_called()

    def test_http_error_does_not_repeat_post(self):
        import urllib.error

        cause = urllib.error.HTTPError(
            "http://127.0.0.1:14501/command",
            500,
            "Internal Server Error",
            None,
            None,
        )
        error = _wrapped_transport_error(cause)
        transport = _Transport(error, "unexpected retry")
        post = core_bridge._make_post_with_retry(transport, _State(), 10)

        with mock.patch.object(core_bridge.time, "sleep") as sleep:
            with self.assertRaises(_Transport.TransportError) as raised:
                post("command", {"action": "create"})

        self.assertIs(error, raised.exception)
        self.assertEqual(1, len(transport.calls))
        sleep.assert_not_called()

    def test_connection_reset_does_not_repeat_post(self):
        error = _wrapped_transport_error(
            ConnectionResetError("reset after dispatch")
        )
        transport = _Transport(error, "unexpected retry")
        post = core_bridge._make_post_with_retry(transport, _State(), 10)

        with mock.patch.object(core_bridge.time, "sleep") as sleep:
            with self.assertRaises(_Transport.TransportError) as raised:
                post("batch", {"commands": [{"action": "create"}]})

        self.assertIs(error, raised.exception)
        self.assertEqual(1, len(transport.calls))
        sleep.assert_not_called()

    def test_other_transport_and_os_errors_do_not_repeat_post(self):
        import errno

        errors = (
            _Transport.TransportError("response body could not be read"),
            OSError(errno.EHOSTUNREACH, "host unreachable"),
        )
        for error in errors:
            with self.subTest(error=error):
                transport = _Transport(error, "unexpected retry")
                post = core_bridge._make_post_with_retry(
                    transport,
                    _State(),
                    10,
                )

                with mock.patch.object(core_bridge.time, "sleep") as sleep:
                    with self.assertRaises(type(error)) as raised:
                        post("command", {})

                self.assertIs(error, raised.exception)
                self.assertEqual(1, len(transport.calls))
                sleep.assert_not_called()

    def test_second_refused_attempt_is_not_retried(self):
        first = _wrapped_transport_error(
            ConnectionRefusedError("domain reload")
        )
        second = _wrapped_transport_error(
            ConnectionRefusedError("still unavailable")
        )
        transport = _Transport(first, second, "unexpected third attempt")
        post = core_bridge._make_post_with_retry(transport, _State(), 10)

        with mock.patch.object(core_bridge.time, "sleep") as sleep:
            with self.assertRaises(_Transport.TransportError) as raised:
                post("command", {"action": "create"})

        self.assertIs(second, raised.exception)
        self.assertEqual(2, len(transport.calls))
        sleep.assert_called_once_with(core_bridge._RETRY_DELAY_S)

    def test_suppressed_stale_context_does_not_trigger_retry(self):
        import urllib.error

        stale = ConnectionRefusedError("stale failure")
        current = _Transport.TransportError("current HTTP failure")
        current.__context__ = stale
        current.__cause__ = urllib.error.HTTPError(
            "http://127.0.0.1:14501/command",
            500,
            "Internal Server Error",
            None,
            None,
        )
        current.__suppress_context__ = True
        transport = _Transport(current, "unexpected retry")
        post = core_bridge._make_post_with_retry(transport, _State(), 10)

        with mock.patch.object(core_bridge.time, "sleep") as sleep:
            with self.assertRaises(_Transport.TransportError) as raised:
                post("command", {"action": "create"})

        self.assertIs(current, raised.exception)
        self.assertEqual(1, len(transport.calls))
        sleep.assert_not_called()

    def test_non_integer_error_codes_do_not_trigger_retry_or_mask_error(self):
        invalid_codes = (True, 10061.0, "10061", [10061])
        for value in invalid_codes:
            with self.subTest(value=value):
                error = _Transport.TransportError("current failure")
                error.errno = value
                error.winerror = value
                transport = _Transport(error, "unexpected retry")
                post = core_bridge._make_post_with_retry(
                    transport,
                    _State(),
                    10,
                )

                with mock.patch.object(core_bridge.time, "sleep") as sleep:
                    with self.assertRaises(_Transport.TransportError) as raised:
                        post("command", {})

                self.assertIs(error, raised.exception)
                self.assertEqual(1, len(transport.calls))
                sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
