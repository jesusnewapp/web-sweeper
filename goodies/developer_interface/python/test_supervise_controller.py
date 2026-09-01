import signal
import subprocess
import unittest
from unittest import mock

import supervise_controller


class StopChildTests(unittest.TestCase):
    @mock.patch.object(supervise_controller.os, "killpg")
    def test_stop_child_terminates_the_controller_process_group(self, killpg):
        child = mock.Mock(pid=42)
        child.poll.return_value = None
        child.wait.return_value = 0

        supervise_controller.stop_child(child)

        killpg.assert_called_once_with(42, signal.SIGTERM)
        child.wait.assert_called_once_with(
            timeout=supervise_controller.STOP_TIMEOUT_SECONDS
        )

    @mock.patch.object(supervise_controller.os, "killpg")
    def test_stop_child_escalates_after_bounded_timeout(self, killpg):
        child = mock.Mock(pid=43)
        child.poll.return_value = None
        child.wait.side_effect = [subprocess.TimeoutExpired("server", 5), 0]

        supervise_controller.stop_child(child)

        self.assertEqual(
            [mock.call(43, signal.SIGTERM), mock.call(43, signal.SIGKILL)],
            killpg.call_args_list,
        )


if __name__ == "__main__":
    unittest.main()
