import socket
import unittest
from unittest.mock import patch

from sweeper.discovery import _read_rss
from sweeper.screening import CandidateWindow


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, _maximum):
        return b"<rss/>"


class DiscoveryRetryTests(unittest.TestCase):
    def test_socket_timeout_retries_without_terminating_discovery(self):
        attempts = 0

        def flaky(*_args, **_kwargs):
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise socket.timeout("slow upstream")
            return _Response()

        with patch("sweeper.discovery.urllib.request.urlopen", flaky), \
                patch("sweeper.discovery.time.sleep"):
            self.assertEqual(b"<rss/>", _read_rss(object(), retries=3))
        self.assertEqual(3, attempts)

    def test_candidate_window_counts_only_new_screened_identities(self):
        window = CandidateWindow(target=3, remembered={"old-1", "old-2"})
        admitted = [
            identity for identity in ("old-1", "new-1", "old-2", "new-1", "new-2", "new-3")
            if window.admit(identity)
        ]
        self.assertEqual(["new-1", "new-2", "new-3"], admitted)
        self.assertEqual(6, window.raw_records)
        self.assertEqual(3, window.remembered_skips)
        self.assertEqual(3, window.screened)
        self.assertTrue(window.full)


if __name__ == "__main__":
    unittest.main()
