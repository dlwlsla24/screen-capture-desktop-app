"""화면 캡처: GNOME 의 Print 키 화면을 그대로 띄운다.

xdg-desktop-portal Screenshot 을 interactive=true 로 부르면 GNOME Shell 이 자기 스크린샷
화면(영역·화면·창 선택)을 띄우고, 찍힌 그림을 클립보드에 복사한 뒤 사진 폴더에 저장하고
그 파일 경로를 돌려준다. 캡처·복사·저장은 전부 GNOME 이 하고, 우리는 결과 파일만 받는다.

직접 화면을 받아 오는 방식(interactive=false)은 쓰지 않는다. GNOME 이 찍을 때마다 화면
전체를 하얗게 번쩍이게 하고(끌 수 없음), 4K 2대 기준 0.7초가 걸린다.
"""

import os
import time

from gi.repository import Gio, GLib

PORTAL = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"

_counter = 0


class NativeCapture:
    """캡처 한 번. on_done(파일 경로) / on_cancel() / on_error(요청 자체가 실패한 경우의 메시지) 중 하나를 부른다."""

    def __init__(self, on_done, on_cancel, on_error):
        self._on_done = on_done
        self._on_cancel = on_cancel
        self._on_error = on_error
        self._bus = None
        self._sub = 0
        self.started = time.monotonic()
        self.finished = False

    def start(self):
        global _counter
        _counter += 1
        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION)
            sender = self._bus.get_unique_name()[1:].replace(".", "_")
            token = f"chalkak_{os.getpid()}_{_counter}"
            self._sub = self._bus.signal_subscribe(
                PORTAL,
                "org.freedesktop.portal.Request",
                "Response",
                f"{PORTAL_PATH}/request/{sender}/{token}",
                None,
                Gio.DBusSignalFlags.NONE,
                self._on_response,
            )
            options = {
                "handle_token": GLib.Variant("s", token),
                "interactive": GLib.Variant("b", True),
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
        except GLib.Error as err:
            self._finish(self._on_error, f"화면 캡처 요청 실패: {err.message}")

    def _on_call_done(self, bus, result):
        try:
            bus.call_finish(result)
        except GLib.Error as err:
            self._finish(self._on_error, f"화면 캡처 요청 실패: {err.message}")

    def _on_response(self, _bus, _sender, _path, _iface, _signal, params):
        code, results = params.unpack()
        if code == 0 and "uri" in results:
            self._finish(self._on_done, Gio.File.new_for_uri(results["uri"]).get_path())
        else:
            # Esc 로 닫아도 GNOME 은 "실패"(코드 2)로 알려 준다. 취소와 구분할 수 없으므로 조용히 끝낸다.
            self._finish(self._on_cancel)

    def _finish(self, callback, *args):
        if self.finished:
            return
        self.finished = True
        self.abandon()
        callback(*args)

    def abandon(self):
        """응답을 더 기다리지 않는다."""
        self.finished = True
        if self._sub and self._bus:
            self._bus.signal_unsubscribe(self._sub)
            self._sub = 0
