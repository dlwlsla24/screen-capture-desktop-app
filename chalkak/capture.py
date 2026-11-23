"""화면 전체를 한 장 찍는다 (xdg-desktop-portal Screenshot, 대화상자 없음).

포털은 결과를 사진 폴더에 PNG 로 떨어뜨리고 그 경로를 알려 준다.
읽은 뒤 그 파일은 바로 지운다(사용자 사진 폴더를 어지르지 않기 위해).
"""

import os
import threading

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gio, GLib  # noqa: E402

PORTAL = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
TIMEOUT_SECONDS = 6

_counter = 0


class ScreenGrab:
    """비동기 전체 화면 캡처. on_done(pixbuf) 또는 on_error(message) 를 한 번 부른다."""

    def __init__(self, on_done, on_error):
        self._on_done = on_done
        self._on_error = on_error
        self._finished = False
        self._sub = 0
        self._timeout = 0
        self._bus = None

    def start(self):
        global _counter
        _counter += 1
        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION)
            sender = self._bus.get_unique_name()[1:].replace(".", "_")
            token = f"chalkak_{os.getpid()}_{_counter}"
            request_path = f"{PORTAL_PATH}/request/{sender}/{token}"
            self._sub = self._bus.signal_subscribe(
                PORTAL,
                "org.freedesktop.portal.Request",
                "Response",
                request_path,
                None,
                Gio.DBusSignalFlags.NONE,
                self._on_response,
            )
            options = {
                "handle_token": GLib.Variant("s", token),
                "interactive": GLib.Variant("b", False),
            }
            self._bus.call(
                PORTAL,
                PORTAL_PATH,
                "org.freedesktop.portal.Screenshot",
                "Screenshot",
                GLib.Variant("(sa{sv})", ("", options)),
                None,
                Gio.DBusCallFlags.NONE,
                -1,
                None,
                self._on_call_done,
            )
            self._timeout = GLib.timeout_add_seconds(TIMEOUT_SECONDS, self._on_timeout)
        except GLib.Error as err:
            self._fail(f"화면 캡처 요청 실패: {err.message}")

    # ── 포털 응답 ─────────────────────────────────────────────────────────
    def _on_call_done(self, bus, result):
        try:
            bus.call_finish(result)
        except GLib.Error as err:
            self._fail(f"화면 캡처 요청 실패: {err.message}")

    def _on_response(self, _bus, _sender, _path, _iface, _signal, params):
        code, results = params.unpack()
        if code != 0 or "uri" not in results:
            self._fail("화면 캡처가 거부되었거나 취소되었습니다.")
            return
        path = Gio.File.new_for_uri(results["uri"]).get_path()
        threading.Thread(target=self._load, args=(path,), daemon=True).start()

    def _load(self, path):
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file(path)
            error = None
        except GLib.Error as err:
            pixbuf, error = None, err.message
        try:
            os.remove(path)
        except OSError:
            pass
        GLib.idle_add(self._loaded, pixbuf, error)

    def _loaded(self, pixbuf, error):
        if pixbuf is None:
            self._fail(f"캡처 이미지를 읽지 못했습니다: {error}")
        elif not self._finished:
            self._cleanup()
            self._on_done(pixbuf)
        return GLib.SOURCE_REMOVE

    def _on_timeout(self):
        self._timeout = 0
        self._fail("화면 캡처 응답이 없습니다.")
        return GLib.SOURCE_REMOVE

    def _fail(self, message):
        if self._finished:
            return
        self._cleanup()
        self._on_error(message)

    def _cleanup(self):
        self._finished = True
        if self._sub and self._bus:
            self._bus.signal_unsubscribe(self._sub)
            self._sub = 0
        if self._timeout:
            GLib.source_remove(self._timeout)
            self._timeout = 0

    def cancel(self):
        """결과를 버린다. (포털이 만든 파일은 응답이 오면 지워진다.)"""
        if self._finished:
            return
        self._on_done = lambda _pixbuf: None
        self._on_error = lambda _message: None
