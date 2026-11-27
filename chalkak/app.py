"""심플찰칵 애플리케이션: 백그라운드에 떠 있다가 단축키(D-Bus 액션)를 받으면 동작한다.

액션 (gapplication action app.chalkak.Chalkak <이름>):
  capture     선택한 영역을 캡처 → 저장하고 클립보드 복사
  select-monitors  캡처할 모니터를 다시 선택한 뒤 영역 캡처
  edit-last   가장 최근 스크린샷을 편집 창으로 열기
  edit <경로> 그 파일을 편집 창으로 열기
  quit        종료
"""

import os
import sys
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from .capture import NativeCapture
from .editor import EditorWindow
from .preview import CapturePreview
from .util import APP_ID, latest_screenshot, load_pixbuf, log

CSS = """
.chalkak-canvas { background-color: #1b1b1e; }
.chalkak-tool { min-width: 36px; min-height: 34px; padding: 0; border-radius: 9px; }
.chalkak-tool:checked { background-color: @accent_bg_color; color: @accent_fg_color; }
.chalkak-pill {
  background-color: alpha(#232327, 0.96); color: white;
  border-radius: 999px; padding: 6px 12px;
  border: 1px solid alpha(white, 0.10);
  box-shadow: 0 6px 22px alpha(black, 0.45);
}
.chalkak-pill label { color: white; }
.chalkak-pill separator { background-color: alpha(white, 0.18); min-width: 1px; margin-top: 5px; margin-bottom: 5px; }
.chalkak-toast { padding: 7px 8px 7px 18px; }
.chalkak-toast button { color: #8ec5ff; padding: 2px 10px; min-height: 0; border-radius: 999px; }
.chalkak-dot { min-width: 30px; min-height: 30px; padding: 0; border-radius: 999px; color: white; }
.chalkak-dot:hover { background-color: alpha(white, 0.12); }
.chalkak-dot:checked { background-color: alpha(white, 0.24); box-shadow: inset 0 0 0 1.5px alpha(white, 0.85); }
"""

NOTIFY_ID = "capture"


STALE_SECONDS = 20  # 선택 화면이 이보다 오래 떠 있으면 단축키를 다시 눌렀을 때 새로 띄운다
RETRY_SECONDS = 1.5  # 화면을 이보다 오래 못 받고 있으면 다시 누른 단축키로 새로 시작한다 (그 전은 연타로 본다)


class ChalkakApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.pending = None  # 진행 중인 캡처
        self.last_path = None
        self.preview = CapturePreview(
            self.edit, self._notify_preview, lambda: self.withdraw_notification(NOTIFY_ID)
        )

    # ── 수명 주기 ─────────────────────────────────────────────────────────
    def do_startup(self):
        Adw.Application.do_startup(self)
        self.hold()  # 창이 없어도 계속 대기 (단축키에 바로 반응 + 편집 후 클립보드 유지)
        provider = Gtk.CssProvider()
        provider.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self._add_action("capture", None, lambda *_: self.capture())
        self._add_action("select-monitors", None, lambda *_: self.capture(reselect=True))
        self._add_action("edit-last", None, lambda *_: self.edit_last())
        self._add_action("edit", "s", lambda _a, param: self.edit(param.get_string()))
        self._add_action("reveal", "s", lambda _a, param: self.reveal(param.get_string()))
        self._add_action("quit", None, lambda *_: self._request_quit())

    def _request_quit(self):
        for window in self.get_windows():
            if isinstance(window, EditorWindow):
                window.close()
        for window in self.get_windows():
            if isinstance(window, EditorWindow):
                window.present()
                return
        self.quit()

    def do_shutdown(self):
        if self.pending is not None:
            self.pending.abandon()
            self.pending = None
        self.preview.dismiss()
        Adw.Application.do_shutdown(self)

    def _add_action(self, name, param_type, callback):
        action = Gio.SimpleAction.new(name, GLib.VariantType.new(param_type) if param_type else None)
        action.connect("activate", callback)
        self.add_action(action)

    def do_activate(self):
        # 앱 목록에서 눌러 실행한 경우: 목록 화면이 닫힐 시간을 준 뒤 캡처한다.
        GLib.timeout_add(450, self._capture_once)

    def _capture_once(self):
        self.capture()
        return GLib.SOURCE_REMOVE

    # ── 캡처 ──────────────────────────────────────────────────────────────
    def capture(self, *, reselect=False):
        if self.pending is not None:
            waited = time.monotonic() - self.pending.started
            if not reselect:
                if self.pending.selecting or self.pending.choosing:
                    # 사용자가 영역이나 모니터를 고르는 중이다. 다시 눌러도 이 화면을 닫지 않는다.
                    if waited < STALE_SECONDS:
                        log("capture: 이미 선택 화면이 떠 있어 무시" if self.pending.selecting
                            else "capture: 화면 공유 선택 창이 떠 있어 무시")
                        return
                elif waited < RETRY_SECONDS:
                    log(f"capture: 화면을 받는 중이라 무시 ({waited * 1000:.0f}ms 경과)")
                    return
                else:
                    log(f"capture: 화면을 {waited * 1000:.0f}ms 동안 못 받아 이전 시도를 버리고 다시 시작")
            self.pending.abandon()
        self.preview.dismiss()
        log("capture: 영역 선택 요청")
        self.pending = NativeCapture(self._captured, self._cancelled, self._capture_failed,
                                     reselect=reselect)
        self.pending.start()

    def _cancelled(self):
        log("capture: 취소")
        self.pending = None

    def _capture_failed(self, message):
        log("capture: 실패 —", message)
        self.pending = None
        self._notify("캡처하지 못했어요", message)

    def _captured(self, path):
        log("capture: 완료 →", path)
        self.pending = None
        self.last_path = path
        self.preview.show(path)

    def _notify_preview(self, path):
        note = Gio.Notification.new("방금 캡처 편집하기")
        note.set_body("누르면 편집 창이 열립니다  ·  Shift+Win+E")
        note.set_icon(Gio.FileIcon.new(Gio.File.new_for_path(path)))
        note.set_priority(Gio.NotificationPriority.NORMAL)
        note.set_default_action_and_target("app.edit", GLib.Variant("s", path))
        self.send_notification(NOTIFY_ID, note)

    # ── 편집 ──────────────────────────────────────────────────────────────
    def edit_last(self):
        path = self.last_path if self.last_path and os.path.exists(self.last_path) else None
        newest = latest_screenshot()
        if newest and (path is None or os.stat(newest).st_mtime_ns > os.stat(path).st_mtime_ns):
            path = newest
        if path is None:
            self._notify("편집할 스크린샷이 없어요", "Shift+Win+2 로 먼저 화면을 캡처하세요.")
            return
        self.edit(path)

    def edit(self, path):
        path = os.path.realpath(path)
        for window in self.get_windows():
            if isinstance(window, EditorWindow) and window.source_path == path:
                self.preview.dismiss()
                self.withdraw_notification(NOTIFY_ID)
                window.present()
                return window
        try:
            pixbuf = load_pixbuf(path)
        except GLib.Error:
            self._notify("스크린샷을 열지 못했어요", "파일이 이미 지워졌습니다. 다시 캡처해 주세요.")
            return
        log("edit:", path)
        self.preview.dismiss()
        self.withdraw_notification(NOTIFY_ID)
        window = EditorWindow(self, pixbuf, self._default_ratio(), path)
        window.present()
        return window

    @staticmethod
    def _default_ratio():
        monitors = Gdk.Display.get_default().get_monitors()
        scales = [monitors.get_item(i).get_scale() for i in range(monitors.get_n_items())]
        return max(scales) if scales else 1.0

    def reveal(self, path):
        Gtk.FileLauncher.new(Gio.File.new_for_path(path)).open_containing_folder(None, None, None)

    def _notify(self, title, body):
        note = Gio.Notification.new(title)
        note.set_body(body)
        self.send_notification(NOTIFY_ID, note)


def main(argv=None):
    return ChalkakApp().run(sys.argv if argv is None else argv)
