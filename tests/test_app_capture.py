"""단축키(capture 액션)를 다시 눌렀을 때: 무시할지, 이전 시도를 버리고 새로 시작할지."""

import unittest
from unittest.mock import Mock, patch

from chalkak.app import RETRY_SECONDS, STALE_SECONDS, ChalkakApp
from chalkak.capture import NativeCapture

START = 1000.0  # 첫 요청 시각 (time.monotonic 대역)


class RepeatCaptureTest(unittest.TestCase):
    def setUp(self):
        self.app = ChalkakApp()
        self.app.preview = Mock()
        self.native = self.enter_patch("chalkak.app.NativeCapture")
        self.native.side_effect = self.new_capture
        self.clock = self.enter_patch("chalkak.app.time.monotonic")
        self.clock.return_value = START
        self.app.capture()
        self.first = self.app.pending

    def enter_patch(self, target):
        patcher = patch(target)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def new_capture(self, *_args, **_kwargs):
        # 실제 NativeCapture 와 같은 공개 속성만 가진 대역
        capture = Mock(spec=NativeCapture)
        capture.started = self.clock.return_value
        capture.selecting = False
        return capture

    def press_again(self, after):
        self.clock.return_value = START + after
        self.app.capture()

    def test_first_press_starts_capture(self):
        self.native.assert_called_once()
        self.first.start.assert_called_once_with()

    def test_ignored_while_selection_shown(self):
        self.first.selecting = True
        for after in (0.2, RETRY_SECONDS + 0.5, STALE_SECONDS - 1):
            self.press_again(after)
        self.assertIs(self.app.pending, self.first)
        self.first.abandon.assert_not_called()
        self.native.assert_called_once()

    def test_ignored_while_receiving_under_retry_window(self):
        for after in (0.1, 0.8, RETRY_SECONDS - 0.01):
            self.press_again(after)
        self.assertIs(self.app.pending, self.first)
        self.first.abandon.assert_not_called()
        self.native.assert_called_once()

    def test_restarts_when_receiving_takes_too_long(self):
        self.press_again(RETRY_SECONDS)
        self.first.abandon.assert_called_once_with()
        self.assertEqual(self.native.call_count, 2)
        second = self.app.pending
        self.assertIsNot(second, self.first)
        second.start.assert_called_once_with()
        # 새 시도도 1.5초 안의 연타는 무시한다.
        self.press_again(RETRY_SECONDS + 0.3)
        self.assertIs(self.app.pending, second)
        second.abandon.assert_not_called()


class SelectingFlagTest(unittest.TestCase):
    def test_set_when_selection_is_shown_and_cleared_on_abandon(self):
        with patch("chalkak.capture.AreaSelection") as overlay:
            capture = NativeCapture(Mock(), Mock(), Mock())
            self.assertFalse(capture.selecting)
            capture._snapshot(Mock())
            overlay.return_value.show.assert_called_once_with()
            self.assertTrue(capture.selecting)
            capture.abandon()
            self.assertFalse(capture.selecting)

    def test_stays_off_when_selection_fails_to_open(self):
        error = Mock()
        with patch("chalkak.capture.AreaSelection") as overlay:
            overlay.return_value.show.side_effect = OSError("picker gone")
            capture = NativeCapture(Mock(), Mock(), error)
            capture._snapshot(Mock())
        self.assertFalse(capture.selecting)
        error.assert_called_once_with("picker gone")


if __name__ == "__main__":
    unittest.main()
