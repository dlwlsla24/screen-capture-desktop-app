"""Exercise the same anonymous socket and image transfer used by the GTK helpers."""

import socket
import unittest
from unittest.mock import Mock, patch

from chalkak import picker_ipc
from chalkak.capture_selection import PickerConnection


class PickerConnectionTest(unittest.TestCase):
    def setUp(self):
        self.client = PickerConnection()
        self.client.sock, self.helper = socket.socketpair(type=socket.SOCK_SEQPACKET)
        self.helper.settimeout(1)
        self.client.sock.settimeout(1)
        self.addCleanup(self.client.sock.close)
        self.addCleanup(self.helper.close)
        self.active = Mock()
        self.client.active = self.active
        self.client.serial = 2

    def read(self):
        self.client._read(None, None)

    def test_transfer_exceeds_socket_buffer_and_preserves_bytes(self):
        # The socket carries only metadata; images stay in anonymous RAM files.
        payload = bytes(range(256)) * 8192
        picker_ipc.send(self.helper, {"id": 2}, payload)
        self.read()
        self.active.selected.assert_called_once_with(payload)
        self.assertIsNone(self.client.active)

    def test_stale_reply_cannot_save_or_dismiss_new_capture(self):
        picker_ipc.send(self.helper, {"id": 1}, b"previous")
        self.read()
        self.assertIs(self.client.active, self.active)
        self.active.selected.assert_not_called()
        self.active.cancelled.assert_not_called()

    def test_disconnect_reports_failure_and_clears_the_grab(self):
        self.helper.close()
        self.assertFalse(self.client._read(None, None))
        self.assertIsNone(self.client.sock)
        self.assertIsNone(self.client.active)
        self.active.failed.assert_called_once()
        self.active.selected.assert_not_called()

    def test_cancel_and_error_do_not_publish(self):
        picker_ipc.send(self.helper, {"id": 2, "error": "grab refused"})
        self.read()
        self.active.failed.assert_called_once_with("grab refused")
        self.client.active = self.active
        picker_ipc.send(self.helper, {"id": 2})
        self.read()
        self.active.cancelled.assert_called_once_with()
        self.active.selected.assert_not_called()

    def test_nudge_names_only_the_requested_monitors(self):
        monitors = []
        for name, (x, y, w, h) in (("DP-3", (0, 0, 3840, 2160)), ("HDMI-1", (3840, 0, 3840, 2160))):
            monitor = Mock()
            monitor.get_connector.return_value = name
            monitor.get_geometry.return_value = Mock(x=x, y=y, width=w, height=h)
            monitors.append(monitor)
        with patch("chalkak.capture_selection.Gdk.Display.get_default") as display:
            display.return_value.get_monitors.return_value = monitors
            self.client.nudge([(0, 0, 3840, 2160)])
            self.client.nudge([(9, 9, 1, 1)])  # unknown region: nothing to send
        message, fd = picker_ipc.receive(self.helper)
        self.assertIsNone(fd)
        self.assertEqual(message, {"command": "nudge", "id": 2,
                                   "monitors": [["DP-3", [0, 0, 3840, 2160]]],
                                   "layout": [[0, 0, 3840, 2160], [3840, 0, 3840, 2160]]})
        with self.assertRaises(TimeoutError):
            self.helper.recv(1)

    def test_nudge_without_helper_is_ignored(self):
        self.client.sock.close()
        self.client.sock = None
        self.client.nudge([(0, 0, 1, 1)])
        self.client.sock = socket.socket()  # for the cleanup registered in setUp

    def test_abandon_only_closes_its_own_grab(self):
        self.client.close(Mock())
        self.assertIs(self.client.active, self.active)
        self.client.close(self.active)
        command, fd = picker_ipc.receive(self.helper)
        self.assertIsNone(fd)
        self.assertEqual({"command": "close", "id": 2}, command)
        self.assertIsNone(self.client.active)


if __name__ == "__main__":
    unittest.main()
