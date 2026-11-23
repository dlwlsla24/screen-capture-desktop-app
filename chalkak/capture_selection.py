"""Send the granted portal pixels to the resident GTK3/XWayland selection helper."""

import os
from pathlib import Path
import socket
import subprocess
import sys

import gi

gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib

from . import picker_ipc
from .picker_geometry import crop_bounds


class PickerConnection:
    def __init__(self):
        self.sock = None
        self.active = None
        self.serial = 0

    def start(self):
        if self.sock is not None:
            return
        parent, child = socket.socketpair(type=socket.SOCK_SEQPACKET)
        try:
            self.process = subprocess.Popen(
                [sys.executable, str(Path(__file__).with_name("picker_window.py")), str(child.fileno())],
                pass_fds=(child.fileno(),), env={**os.environ, "GDK_BACKEND": "x11"},
            )
        except OSError:
            parent.close()
            raise
        finally:
            child.close()
        def reaped(_pid, status, process):
            process.returncode = os.waitstatus_to_exitcode(status)

        GLib.child_watch_add(GLib.PRIORITY_DEFAULT, self.process.pid, reaped, self.process)
        self.sock = parent
        GLib.io_add_watch(parent.fileno(), GLib.PRIORITY_DEFAULT,
                          GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, self._read)

    def _read(self, _fd, _condition):
        try:
            message, fd = picker_ipc.receive(self.sock)
        except (OSError, ValueError, EOFError):
            self.sock.close()
            self.sock = None
            active, self.active = self.active, None
            if active:
                active.failed("XWayland 영역 선택기를 열 수 없습니다.")
            return GLib.SOURCE_REMOVE
        try:
            active = self.active
            if active and message["id"] == self.serial:
                self.active = None
                if "error" in message:
                    active.failed(message["error"])
                elif fd is None:
                    active.cancelled()
                else:
                    try:
                        png = GLib.MappedFile.new_from_fd(fd, False).get_bytes().get_data()
                    except GLib.Error as exc:
                        active.failed(str(exc))
                    else:
                        active.selected(png)
        finally:
            if fd is not None:
                os.close(fd)
        return GLib.SOURCE_CONTINUE

    def show(self, selection):
        self.start()
        self.serial += 1
        image = selection.snapshot
        message = dict(id=self.serial, command="show", monitors=selection.outputs,
                       layout=selection.layout, width=image.get_width(), height=image.get_height(),
                       alpha=image.get_has_alpha(), stride=image.get_rowstride())
        picker_ipc.send(self.sock, message, image.get_pixels())
        self.active = selection

    def close(self, selection):
        if self.active is selection:
            self.active = None
            try:
                picker_ipc.send(self.sock, dict(command="close", id=self.serial))
            except OSError:
                pass  # The child disconnected and the GLib watch will reap it.


picker = PickerConnection()


class AreaSelection:
    def __init__(self, snapshot, selected, cancelled, failed=None):
        self.snapshot = snapshot
        self.selected, self.cancelled = selected, cancelled
        self.failed = failed or (lambda _message: cancelled())
        granted = getattr(snapshot, "monitor_regions", None)
        self.monitors = []
        self.outputs = []
        self.layout = []
        self.regions = []
        for monitor in Gdk.Display.get_default().get_monitors():
            r = monitor.get_geometry()
            region = (r.x, r.y, r.width, r.height)
            self.layout.append(region)
            if granted is None or region in granted:
                self.monitors.append((monitor, r))
                self.outputs.append((monitor.get_connector(), region))
                self.regions.append(region)
        if not self.regions or (granted is not None and len(self.regions) != len(granted)):
            raise ValueError("모니터 배치가 변경되었습니다. 다시 캡처해 주세요.")
        self.left = min(r[0] for r in self.regions)
        self.top = min(r[1] for r in self.regions)

    def crop(self, x0, y0, x1, y1):
        bounds = crop_bounds(self.regions, self.snapshot.get_width(), self.snapshot.get_height(),
                             (x0, y0), (x1, y1))
        return self.snapshot.new_subpixbuf(*bounds) if bounds else None

    def show(self):
        picker.show(self)
        self.snapshot = None

    def close(self):
        picker.close(self)
        self.snapshot = None
