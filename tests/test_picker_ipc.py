"""Exercise the same anonymous socket and image transfer used by the GTK helpers."""

import socket
import time
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

    @staticmethod
    def monitor(name, region):
        x, y, w, h = region
        result = Mock()
        result.get_connector.return_value = name
        result.get_geometry.return_value = Mock(x=x, y=y, width=w, height=h)
        return result

    def test_nudge_names_only_the_requested_monitors(self):
        monitors = [self.monitor("DP-3", (0, 0, 3840, 2160)),
                    self.monitor("HDMI-1", (3840, 0, 3840, 2160))]
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
        self.client.sock.close()  # setUp 의 정리는 이미 닫힌 소켓을 다시 닫을 뿐이다
        self.client.sock = None
        self.client.nudge([(0, 0, 1, 1)])

    def test_nudge_never_blocks_on_a_full_send_buffer(self):
        # 선택기가 살아 있지만 읽지 못하는 상태. 실제 연결(start)처럼 시간 제한 없는
        # blocking 소켓에서, 버퍼가 찬 뒤 보내는 nudge 는 기다리지 않고 버려져야 한다.
        self.client.sock.settimeout(None)
        filler = b"x" * 64
        while True:
            try:
                self.client.sock.send(filler, socket.MSG_DONTWAIT)
            except BlockingIOError:
                break
        with patch("chalkak.capture_selection.Gdk.Display.get_default") as display:
            display.return_value.get_monitors.return_value = [self.monitor("DP-3", (0, 0, 3840, 2160))]
            began = time.monotonic()
            for _ in range(50):  # 한 캡처에서 보낼 수 있는 만큼
                self.client.nudge([(0, 0, 3840, 2160)])
            self.assertLess(time.monotonic() - began, 0.5)
            # 버려진 nudge 는 큐에 없고, 선택기가 다시 읽기 시작하면 다음 nudge 는 전달된다.
            self.helper.setblocking(False)
            while True:
                try:
                    self.assertEqual(self.helper.recv(4096), filler)
                except BlockingIOError:
                    break
            self.helper.settimeout(1)
            self.client.nudge([(0, 0, 3840, 2160)])
        message, fd = picker_ipc.receive(self.helper)
        self.assertIsNone(fd)
        self.assertEqual(message["command"], "nudge")

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
