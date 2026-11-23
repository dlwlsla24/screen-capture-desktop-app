"""Preview lifecycle tests. Set CHALKAK_TEST_PREVIEW_DISPLAY=1 for a synthetic X11 popup."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from chalkak.preview import CapturePreview, EDIT


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.edit, self.notify, self.withdraw = Mock(), Mock(), Mock()
        self.preview = CapturePreview(self.edit, self.notify, self.withdraw)
        self.addCleanup(self.preview.dismiss)

    @patch.dict(os.environ, DISPLAY=":test")
    @patch("chalkak.preview.Gio.SubprocessLauncher.new")
    def test_click_after_replacement(self, launcher):
        first, second = Mock(), Mock()
        launcher.return_value.spawnv.side_effect = [first, second]
        self.preview.show("/one.png")
        self.preview.show("/two.png")
        first.force_exit.assert_called_once()
        first.wait.assert_called_once()
        first.wait_async.call_args.args[1](first, None, "/one.png")
        second.get_exit_status.return_value = EDIT
        second.wait_async.call_args.args[1](second, None, "/two.png")
        self.edit.assert_called_once_with("/two.png")
        self.notify.assert_not_called()

    @patch.dict(os.environ, DISPLAY="")
    def test_notification_dismissal(self):
        self.preview.show("/fallback.png")
        self.notify.assert_called_once_with("/fallback.png")
        self.preview.dismiss()
        self.withdraw.assert_called_once()
        self.assertEqual(self.preview.notification_timer, 0)

    @patch.dict(os.environ, DISPLAY=":test")
    @patch("chalkak.preview.Gio.SubprocessLauncher.new")
    def test_helper_exit(self, launcher):
        process = launcher.return_value.spawnv.return_value
        for status in (0, 1):
            self.preview.show("/test.png")
            process.get_exit_status.return_value = status
            self.preview._finished(process, None, "/test.png")
        self.notify.assert_called_once_with("/test.png")
        self.edit.assert_not_called()

    @unittest.skipUnless(os.getenv("CHALKAK_TEST_PREVIEW_DISPLAY"), "requires X11/XWayland")
    def test_real_window(self):
        subprocess.run([sys.executable, __file__, "--window"], check=True,
                       env={**os.environ, "GDK_BACKEND": "x11"}, timeout=12)


def check_window():
    # Run GTK3 in a separate interpreter, as the GTK4 application does.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "chalkak"))
    import preview_window as helper
    from gi.repository import Gdk, GdkPixbuf, GLib, Gtk

    with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as tmp:
        path = os.path.join(tmp, "synthetic.png")
        pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 640, 360)
        pixbuf.fill(0x398dddff)
        pixbuf.savev(path, "png", [], [])
        observed = []

        def inspect():
            window, = [w for w in Gtk.Window.list_toplevels() if w.get_name() == "chalkak-preview"]
            display = Gdk.Display.get_default()
            area = display.get_monitor_at_window(window.get_window()).get_workarea()
            x, y = window.get_position()
            width, height = window.get_size()
            observed.extend((window.get_mapped(), window.get_accept_focus(),
                             area.x + area.width - x - width, area.y + area.height - y - height))
            window.get_child().get_children()[0].clicked()
            return False

        GLib.timeout_add(450, inspect)
        assert helper.main(path) == EDIT
        assert observed == [True, False, 18, 18], observed

        def dismiss():
            window, = [w for w in Gtk.Window.list_toplevels() if w.get_name() == "chalkak-preview"]
            window.get_child().get_children()[1].get_children()[-1].clicked()
            return False

        GLib.timeout_add(300, dismiss)
        assert helper.main(path) == 0
        with patch.object(helper, "TIMEOUT_MS", 300):
            assert helper.main(path) == 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--window"]:
        check_window()
    else:
        unittest.main()
