"""찰칵 애플리케이션: 백그라운드에 떠 있다가 단축키(D-Bus 액션)를 받으면 캡처한다.

액션 (gapplication action app.chalkak.Chalkak <이름>):
  capture     영역 선택 캡처 → 바로 클립보드 복사
  edit-last   방금 캡처한 화면을 편집 창으로 열기
  edit <경로> 그 파일을 편집 창으로 열기
  save <경로> 그 파일을 사진 폴더에 저장
  quit        종료
"""

import os
import shutil
import sys
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from .editor import EditorWindow
from .overlay import SelectionOverlay
from .util import APP_ID, cache_dir, load_pixbuf, log, new_save_path, prune_cache

CSS = """
window.chalkak-overlay { background: none; }
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


class ChalkakApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.session = None  # 진행 중인 영역 선택
        self.last_path = None
        self.ratios = {}  # 캡처 파일 → 이미지/화면 배율

    # ── 수명 주기 ─────────────────────────────────────────────────────────
    def do_startup(self):
        Adw.Application.do_startup(self)
        self.hold()  # 창이 없어도 계속 대기 (단축키 응답 속도 + 클립보드 유지)
        provider = Gtk.CssProvider()
        provider.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self._add_action("capture", None, lambda *_: self.capture())
        self._add_action("edit-last", None, lambda *_: self.edit_last())
        self._add_action("edit", "s", lambda _a, param: self.edit(param.get_string()))
        self._add_action("save", "s", lambda _a, param: self.save(param.get_string()))
        self._add_action("reveal", "s", lambda _a, param: self.reveal(param.get_string()))
        self._add_action("quit", None, lambda *_: self.quit())

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
    def capture(self):
        if self.session is not None:
            log("capture: 이미 선택 중이라 무시")
            return
        log("capture: 시작")
        self.withdraw_notification(NOTIFY_ID)
        self.session = SelectionOverlay(self, self._captured, self._cancelled, self._capture_failed)

    def _cancelled(self):
        log("capture: 취소")
        self.session = None

    def _capture_failed(self, message):
        log("capture: 실패 —", message)
        self.session = None
        self._notify("캡처하지 못했어요", message)

    def _captured(self, pixbuf, png, ratio):
        self.session = None
        path = os.path.join(cache_dir(), f"capture-{time.strftime('%Y%m%d-%H%M%S')}-{int(time.time() * 1000) % 1000:03d}.png")
        try:
            with open(path, "wb") as handle:
                handle.write(png)
        except OSError:
            return
        log(f"capture: 복사 완료 {pixbuf.get_width()}x{pixbuf.get_height()} → {path}")
        self.last_path = path
        self.ratios[path] = ratio
        prune_cache()
        # 전체 화면 창이 완전히 닫힌 뒤에 보내야 알림 배너가 뜬다.
        GLib.timeout_add(250, self._notify_captured, path, pixbuf.get_width(), pixbuf.get_height())

    def _notify_captured(self, path, width, height):
        note = Gio.Notification.new("스크린샷을 복사했어요")
        note.set_body(f"편집: 이 알림 클릭 또는 Shift+Win+E  ·  {width}×{height}")
        note.set_icon(Gio.FileIcon.new(Gio.File.new_for_path(path)))
        target = GLib.Variant("s", path)
        note.set_default_action_and_target("app.edit", target)
        note.add_button_with_target("편집", "app.edit", target)
        note.add_button_with_target("파일로 저장", "app.save", target)
        self.send_notification(NOTIFY_ID, note)
        return GLib.SOURCE_REMOVE

    # ── 편집 ──────────────────────────────────────────────────────────────
    def edit_last(self):
        path = self.last_path
        if not path or not os.path.exists(path):
            folder = cache_dir()
            files = sorted(f for f in os.listdir(folder) if f.startswith("capture-") and f.endswith(".png"))
            path = os.path.join(folder, files[-1]) if files else None
        if path is None:
            self._notify("편집할 캡처가 없어요", "Shift+Win+2 로 먼저 화면을 캡처하세요.")
            return
        self.edit(path)

    def edit(self, path):
        try:
            pixbuf = load_pixbuf(path)
        except GLib.Error:
            self._notify("캡처를 열지 못했어요", "파일이 이미 지워졌습니다. 다시 캡처해 주세요.")
            return
        log("edit:", path)
        self.withdraw_notification(NOTIFY_ID)
        window = EditorWindow(self, pixbuf, self.ratios.get(path, self._default_ratio()), path)
        window.present()
        return window

    @staticmethod
    def _default_ratio():
        monitors = Gdk.Display.get_default().get_monitors()
        scales = [monitors.get_item(i).get_scale() for i in range(monitors.get_n_items())]
        return max(scales) if scales else 1.0

    # ── 저장 ──────────────────────────────────────────────────────────────
    def save(self, path):
        target = new_save_path()
        try:
            shutil.copyfile(path, target)
        except OSError as err:
            self._notify("저장하지 못했어요", err.strerror or str(err))
            return
        note = Gio.Notification.new("파일로 저장했어요")
        note.set_body(target.replace(os.path.expanduser("~"), "~", 1))
        note.set_default_action_and_target("app.reveal", GLib.Variant("s", target))
        self.send_notification(NOTIFY_ID, note)

    def reveal(self, path):
        Gtk.FileLauncher.new(Gio.File.new_for_path(path)).open_containing_folder(None, None, None)

    def _notify(self, title, body):
        note = Gio.Notification.new(title)
        note.set_body(body)
        self.send_notification(NOTIFY_ID, note)


def main(argv=None):
    return ChalkakApp().run(sys.argv if argv is None else argv)
