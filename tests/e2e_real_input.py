"""설치된 심플찰칵을 진짜 단축키·마우스로 끝까지 돌려 본다 (GNOME 전용, 개발용).

Mutter RemoteDesktop 으로 가상 입력을 넣는다. 실행하는 약 8초 동안 실제 키보드·마우스가
움직이므로 손을 떼고 있어야 한다.
  Shift+Super+2 → 드래그 → 마우스 놓기
  → 새 파일·클립보드 확인 → Shift+Super+E → 편집 창 확인 → Esc

첫 사용 권한 안내를 한 번 완료한 뒤 실행: /usr/bin/python3 tests/e2e_real_input.py
테스트가 만든 스크린샷 파일은 끝날 때 지운다. 클립보드에는 테스트 캡처가 남는다.
"""

import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from chalkak.util import latest_screenshot  # noqa: E402

RD = "org.gnome.Mutter.RemoteDesktop"
SHIFT, SUPER, KEY_2, KEY_E, ESC = 42, 125, 3, 18, 1  # evdev 키 코드

bus = Gio.bus_get_sync(Gio.BusType.SESSION)


def call(path, iface, method, args=None, reply=None):
    return bus.call_sync(RD, path, iface, method, args, reply, 0, -1, None)


session = call("/org/gnome/Mutter/RemoteDesktop", RD, "CreateSession", None, GLib.VariantType("(o)")).unpack()[0]
SESSION = RD + ".Session"
call(session, SESSION, "Start")


def key(code, down):
    call(session, SESSION, "NotifyKeyboardKeycode", GLib.Variant("(ub)", (code, down)))


def chord(*codes):
    for code in codes:
        key(code, True)
        time.sleep(0.03)
    for code in reversed(codes):
        key(code, False)
        time.sleep(0.03)


def drag(dx, dy, steps=10):
    call(session, SESSION, "NotifyPointerButton", GLib.Variant("(ib)", (272, True)))
    time.sleep(0.05)
    for _ in range(steps):
        call(session, SESSION, "NotifyPointerMotionRelative", GLib.Variant("(dd)", (dx / steps, dy / steps)))
        time.sleep(0.02)
    time.sleep(0.1)
    call(session, SESSION, "NotifyPointerButton", GLib.Variant("(ib)", (272, False)))


def journal(since):
    out = subprocess.run(
        ["journalctl", "--user", "-t", "app.chalkak.Chalkak", "--since", since, "-o", "cat", "--no-pager"],
        capture_output=True,
        text=True,
    ).stdout
    return [line for line in out.splitlines() if "capture:" in line or "edit:" in line]


class Checker(Gtk.Application):
    """클립보드는 포커스를 가진 창만 읽을 수 있어, 작은 창을 띄워 확인한다."""

    def __init__(self):
        super().__init__(application_id="app.chalkak.E2E", flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.failures = []
        self.before = latest_screenshot()
        self.since = time.strftime("%Y-%m-%d %H:%M:%S")
        self.created = None

    def check(self, ok, text):
        print(("OK   " if ok else "FAIL ") + text, flush=True)
        if not ok:
            self.failures.append(text)

    def do_activate(self):
        self.hold()
        GLib.timeout_add(300, self.step_capture)

    def step_capture(self):
        chord(SHIFT, SUPER, KEY_2)
        time.sleep(1.6)  # 포털 스냅샷과 선택 창을 기다린다.
        drag(300, 200)
        GLib.timeout_add(900, self.step_verify_capture)
        return GLib.SOURCE_REMOVE

    def step_verify_capture(self):
        newest = latest_screenshot()
        self.created = newest if newest != self.before else None
        self.check(self.created is not None, f"새 스크린샷 파일: {self.created}")
        self.check(any("capture: 완료" in line for line in journal(self.since)), "앱이 캡처 완료를 받음")
        self.window = Gtk.ApplicationWindow(application=self, title="e2e", default_width=220, default_height=80)
        self.window.present()
        GLib.timeout_add(500, self.step_read_clipboard)
        return GLib.SOURCE_REMOVE

    def step_read_clipboard(self):
        clipboard = Gdk.Display.get_default().get_clipboard()
        self.check("image/png" in clipboard.get_formats().to_string(), "클립보드에 이미지가 있음")
        self.window.close()
        GLib.timeout_add(400, self.step_edit)
        return GLib.SOURCE_REMOVE

    def step_edit(self):
        chord(SHIFT, SUPER, KEY_E)
        GLib.timeout_add(1300, self.step_verify_edit)
        return GLib.SOURCE_REMOVE

    def step_verify_edit(self):
        lines = journal(self.since)
        opened = [line for line in lines if "edit:" in line]
        self.check(bool(opened) and self.created is not None and self.created in opened[-1], "편집 단축키가 방금 캡처를 엶")
        chord(ESC)
        GLib.timeout_add(500, self.finish)
        return GLib.SOURCE_REMOVE

    def finish(self):
        call(session, SESSION, "Stop")
        if self.created and os.path.exists(self.created):
            os.remove(self.created)
        self.quit()
        return GLib.SOURCE_REMOVE


if __name__ == "__main__":
    app = Checker()
    app.run([])
    print("결과:", "통과" if not app.failures else f"실패 {len(app.failures)}건")
    sys.exit(1 if app.failures else 0)
