"""Take one snapshot using a short-lived, monitor-only XDG ScreenCast session."""

import json
import os
import tempfile
import uuid
from pathlib import Path

from gi.repository import Gio, GLib

from .capture_frame import read_snapshot
from .util import APP_ID

PORTAL = "org.freedesktop.portal.Desktop"
DESKTOP = "/org/freedesktop/portal/desktop"
CAST = "org.freedesktop.portal.ScreenCast"
REQUEST = "org.freedesktop.portal.Request"
SESSION = "org.freedesktop.portal.Session"
FLAGS = Gio.DBusCallFlags.NONE
# GNOME can return Start while its chooser is still fading out.
CHOOSER_SETTLE_MS = 450
FAST_RESTORE_US = 150_000


def _active_monitors(bus):
    """Return Mutter's active physical monitor specs in stable connector order."""
    interface = "org.gnome.Mutter.DisplayConfig"
    try:
        reply = bus.call_sync(interface, "/org/gnome/Mutter/DisplayConfig", interface,
                              "GetCurrentState", None, None, FLAGS, 1000, None)
    except GLib.Error:
        return []
    # Logical monitors contain the active (connector, vendor, product, serial)
    # specs, including each physical output in a mirrored group.
    logical = reply.get_child_value(2).unpack()
    return sorted(list(spec) for monitor in logical for spec in monitor[5])


class ScreenCast:
    def __init__(self, ready, cancelled, error, *, reselect=False, nudge=None):
        self.ready = ready
        self.cancelled = cancelled
        self.error = error
        self.nudge = nudge  # nudge(regions): 멈춰 있는 모니터를 다시 그리게 한다 (capture_frame 참고)
        self.token_path = Path(GLib.get_user_state_dir(), "chalkak", "screencast-restore-token")
        self.bus = None
        self.session = None
        self.request = None
        self.response_sub = 0
        self.closed_sub = 0
        self.owner_sub = 0
        self.reader = None
        self.reselect = reselect
        self.monitors = []
        self.restore_requested = False
        self.settle_source = 0
        self.closed = False
        # GNOME 화면 공유 선택 창이 떠 있다: 토큰 없이 SelectSources 를 보낸 뒤 Start 응답(_started)까지.
        # 사용자가 모니터를 고르는 중이므로 단축키를 다시 눌러도 이 시도를 버리지 않는다.
        self.choosing = False

    def start(self):
        if self.closed:
            return
        try:
            address = Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION)
            flags = (Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
                     | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION)
            self.bus = Gio.DBusConnection.new_for_address_sync(address, flags, None, None)
            self.bus.set_exit_on_close(False)
            self.bus.connect("closed", self._bus_closed)
            try:
                self.bus.call_sync(
                    PORTAL, DESKTOP, "org.freedesktop.host.portal.Registry", "Register",
                    GLib.Variant("(sa{sv})", (APP_ID, {})), None, FLAGS, 5000, None)
            except GLib.Error as err:
                if Gio.DBusError.get_remote_error(err) not in {
                    "org.freedesktop.DBus.Error.UnknownMethod",
                    "org.freedesktop.DBus.Error.UnknownInterface",
                }:
                    raise
            self.owner_sub = self.bus.signal_subscribe(
                "org.freedesktop.DBus", "org.freedesktop.DBus", "NameOwnerChanged",
                "/org/freedesktop/DBus", PORTAL, Gio.DBusSignalFlags.NONE, self._owner_changed)
            props, = self.bus.call_sync(
                PORTAL, DESKTOP, "org.freedesktop.DBus.Properties", "GetAll",
                GLib.Variant("(s)", (CAST,)), None, FLAGS, 5000, None).unpack()
            if not props.get("AvailableSourceTypes", 0) & 1:
                raise ValueError("이 시스템에서는 모니터 화면 공유를 사용할 수 없습니다.")
            self.persist = props.get("version", 0) >= 4
            self.sender = self.bus.get_unique_name()[1:].replace(".", "_")
            session_token = "chalkak_" + uuid.uuid4().hex
            self._call_request("CreateSession", "a{sv}", (), {
                "session_handle_token": GLib.Variant("s", session_token),
            }, self._created)
        except (GLib.Error, ValueError) as err:
            self._finish(self.error, str(err))

    def _bus_closed(self, *_):
        self._finish(self.error, "화면 공유 서비스와의 연결이 끊어졌습니다.")

    def _owner_changed(self, _bus, _name, _path, _iface, _signal, params):
        if params.unpack()[1]:
            self._bus_closed()

    def _call_request(self, method, signature, args, options, response):
        token = "chalkak_" + uuid.uuid4().hex
        options["handle_token"] = GLib.Variant("s", token)
        self.request = f"{DESKTOP}/request/{self.sender}/{token}"
        self.response_sub = self.bus.signal_subscribe(
            PORTAL, REQUEST, "Response", self.request, None, Gio.DBusSignalFlags.NONE,
            self._response, response)
        self.bus.call(PORTAL, DESKTOP, CAST, method, GLib.Variant(
            f"({signature})", (*args, options)), None, FLAGS, -1, None,
            self._request_ack, self.request)

    def _request_ack(self, bus, result, path):
        try:
            actual, = bus.call_finish(result).unpack()
            if actual != path:
                raise ValueError("화면 공유 요청 경로가 일치하지 않습니다.")
        except (GLib.Error, ValueError) as err:
            if path == self.request:
                self._finish(self.error, str(err))

    def _response(self, bus, _name, path, _iface, _signal, params, response):
        if self.closed or path != self.request:
            return
        bus.signal_unsubscribe(self.response_sub)
        self.response_sub = 0
        self.request = None
        code, results = params.unpack()
        if code == 1:
            self._finish(self.cancelled)
        elif code != 0:
            self._finish(self.error, "시스템에서 화면 공유 요청을 완료하지 못했습니다.")
        else:
            try:
                response(results)
            except (GLib.Error, OSError, KeyError, ValueError) as err:
                self._finish(self.error, str(err))

    def _created(self, results):
        self.session = results["session_handle"]
        self.closed_sub = self.bus.signal_subscribe(
            PORTAL, SESSION, "Closed", self.session, None, Gio.DBusSignalFlags.NONE,
            self._session_closed)
        options = {"types": GLib.Variant("u", 1), "multiple": GLib.Variant("b", True)}
        if self.persist:
            options["persist_mode"] = GLib.Variant("u", 2)
            self.monitors = _active_monitors(self.bus)
            token = self._load_token() if self.monitors and not self.reselect else None
            if token:
                options["restore_token"] = GLib.Variant("s", token)
                self.restore_requested = True
        self.choosing = not self.restore_requested  # 토큰으로 조용히 복원하면 선택 창이 뜨지 않는다
        self._call_request("SelectSources", "oa{sv}", (self.session,), options, self._selected)

    def _session_closed(self, *_):
        self.session = None
        self._finish(self.cancelled)

    def _selected(self, _results):
        self.start_time_us = GLib.get_monotonic_time()
        self._call_request("Start", "osa{sv}", (self.session, ""), {}, self._started)

    def _started(self, results):
        self.choosing = False
        elapsed_us = GLib.get_monotonic_time() - self.start_time_us
        streams = results.get("streams")
        if not streams:
            raise ValueError("공유할 모니터가 없습니다.")
        if self.persist:
            # A hotplug while the chooser was open leaves the grant ambiguous.
            stable = self.monitors and self.monitors == _active_monitors(self.bus)
            self._store_token(results.get("restore_token") if stable else None)
        # Start does not identify whether restoration succeeded silently. A slow
        # Start with a token can indicate that GNOME asked for consent again.
        if not self.restore_requested or elapsed_us >= FAST_RESTORE_US:
            self.settle_source = GLib.timeout_add(CHOOSER_SETTLE_MS, self._open_remote, streams)
        else:
            self._open_remote(streams)

    def _open_remote(self, streams):
        self.settle_source = 0
        if self.closed:
            return GLib.SOURCE_REMOVE
        try:
            self.bus.call_with_unix_fd_list(
                PORTAL, DESKTOP, CAST, "OpenPipeWireRemote",
                GLib.Variant("(oa{sv})", (self.session, {})), None, FLAGS, -1,
                None, None, self._remote_opened, streams)
        except GLib.Error as err:
            self._finish(self.error, str(err))
        return GLib.SOURCE_REMOVE

    def _load_token(self):
        try:
            raw = self.token_path.read_text(encoding="utf-8").strip()
            if not raw.startswith("{"):
                # Legacy files carry no monitor identity. One active monitor
                # cannot be restored as a partial selection.
                return raw if len(self.monitors) == 1 and "\x00" not in raw else None
            saved = json.loads(raw)
            if saved.get("version") == 1 and saved.get("monitors") == self.monitors:
                token = saved.get("token")
                if isinstance(token, str) and "\x00" not in token:
                    return token
        except (OSError, ValueError, RecursionError):
            pass
        return None

    def _store_token(self, token):
        temporary = None
        try:
            if not token:
                self.token_path.unlink(missing_ok=True)
                return
            self.token_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False,
                                             dir=self.token_path.parent) as output:
                temporary = Path(output.name)
                json.dump({"version": 1, "monitors": self.monitors, "token": token}, output)
            os.replace(temporary, self.token_path)
            temporary = None
        except OSError:
            pass  # Capturing still works when preferences cannot be persisted.
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def _remote_opened(self, bus, result, streams):
        try:
            reply, fds = bus.call_with_unix_fd_list_finish(result)
            if self.closed:
                return
            index, = reply.unpack()
            reader = read_snapshot(fds.get(index), streams,
                                   lambda pixbuf: self._finish(self.ready, pixbuf),
                                   lambda reason: self._finish(self.error, str(reason)),
                                   nudge=self.nudge)
            if self.closed:
                reader.cancel()
            else:
                self.reader = reader
        except (GLib.Error, OSError, ImportError, RuntimeError, ValueError) as err:
            self._finish(self.error, str(err))

    def _finish(self, callback, *args):
        if not self.closed:
            self.close()
            callback(*args)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.choosing = False
        if self.settle_source:
            GLib.source_remove(self.settle_source)
            self.settle_source = 0
        if self.reader:
            self.reader.cancel()
            self.reader = None
        if self.bus:
            for sub in (self.response_sub, self.closed_sub, self.owner_sub):
                if sub:
                    self.bus.signal_unsubscribe(sub)
            if not self.bus.is_closed():
                for path, iface in ((self.request, REQUEST), (self.session, SESSION)):
                    if path:
                        self.bus.call(PORTAL, path, iface, "Close", None, None,
                                      FLAGS, -1, None, None)
                # Disconnection also releases any session created while closing.
                self.bus.close(None, None)
            self.bus = None
        self.request = self.session = None
