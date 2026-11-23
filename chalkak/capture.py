"""Select an area from a transient portal ScreenCast snapshot."""

import time

from gi.repository import GLib

from .capture_selection import AreaSelection, picker
from .screencast import ScreenCast
from .util import save_png


class NativeCapture:
    def __init__(self, on_done, on_cancel, on_error, *, reselect=False):
        self._on_done = on_done
        self._on_cancel = on_cancel
        self._on_error = on_error
        self._selection = None
        self._cast = ScreenCast(self._snapshot, self._cancel, self._failed, reselect=reselect)
        self.started = time.monotonic()
        self.finished = False

    def start(self):
        try:
            picker.start()  # Initialize GTK3/X11 while the consented frame is acquired.
        except OSError as err:
            self._failed(str(err))
            return
        self._cast.start()

    def _failed(self, message):
        self._finish(self._on_error, message)

    def _snapshot(self, snapshot):
        if self.finished:
            return
        try:
            self._selection = AreaSelection(snapshot, self._selected, self._cancel, self._failed)
            self._selection.show()
        except (GLib.Error, OSError, ValueError) as err:
            self._failed(str(err))

    def _selected(self, png):
        if self.finished:
            return
        try:
            path = save_png(png)
        except (GLib.Error, OSError) as err:
            self._finish(self._on_error, str(err))
            return
        self._finish(self._on_done, path)

    def _cancel(self):
        self._finish(self._on_cancel)

    def _finish(self, callback, *args):
        if not self.finished:
            self.abandon()
            callback(*args)

    def abandon(self):
        self.finished = True
        self._cast.close()
        if self._selection:
            self._selection.close()
            self._selection = None
