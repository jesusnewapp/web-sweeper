import unittest

from server import request_path


class ServerRouteTest(unittest.TestCase):
    def test_cache_buster_does_not_change_status_route(self) -> None:
        self.assertEqual(
            "/api/status",
            request_path("/api/status?refresh=1786673000000000"),
        )

    def test_plain_route_is_unchanged(self) -> None:
        self.assertEqual("/api/action", request_path("/api/action"))


if __name__ == "__main__":
    unittest.main()
