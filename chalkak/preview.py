"""Run the positioned GTK3/X11 thumbnail separately from the GTK4 editor."""

import os
import sys

from gi.repository import Gio, GLib

EDIT = 10
TIMEOUT_MS = 8000


class CapturePreview:
    def __init__(self, edit, notify, withdraw):
        self.edit = edit
        self.notify = notify
        self.withdraw = withdraw
        self.process = None
        self.notification_timer = 0

    def show(self, path):
        self.dismiss()
        if os.environ.get("DISPLAY"):
            helper = os.path.join(os.path.dirname(__file__), "preview_window.py")
            launcher = Gio.SubprocessLauncher.new(Gio.SubprocessFlags.NONE)
            launcher.setenv("GDK_BACKEND", "x11", True)
            try:
                self.process = launcher.spawnv([sys.executable, helper, path])
            except GLib.Error:
                pass
            else:
                self.process.wait_async(None, self._finished, path)
                return
        self._fallback(path)

    def _finished(self, process, result, path):
        process.wait_finish(result)
        if self.process is not process:
            return
        self.process = None
        status = process.get_exit_status() if process.get_if_exited() else -1
        if status == EDIT:
            self.edit(path)
        elif status != 0:
            self._fallback(path)

    def _fallback(self, path):
        self.notify(path)
        self.notification_timer = GLib.timeout_add(TIMEOUT_MS, self._expire)

    def _expire(self):
        self.notification_timer = 0
        self.withdraw()
        return GLib.SOURCE_REMOVE

    def dismiss(self):
        if self.process is not None:
            process, self.process = self.process, None
            process.force_exit()
            process.wait(None)
        if self.notification_timer:
            GLib.source_remove(self.notification_timer)
            self._expire()
