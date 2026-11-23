"""편집 창을 실제로 1~2초 띄워, 마우스·키보드 처리 경로를 코드로 흉내 내 검증한다.

실행: /usr/bin/python3 tests/test_editor_window.py
(화면에 편집 창이 잠깐 떴다가 닫힌다. 클립보드에 테스트 이미지가 올라간다.)
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib  # noqa: E402

from chalkak.app import ChalkakApp  # noqa: E402
from chalkak.editor import EditorWindow  # noqa: E402
from test_render import make_background  # noqa: E402

SHIFT = Gdk.ModifierType.SHIFT_MASK
CTRL = Gdk.ModifierType.CONTROL_MASK


class FakeGesture:
    """GtkGestureDrag 대역: 시작점과 보조키 상태만 알려 준다."""

    def __init__(self, x, y, state=0):
        self.start = (x, y)
        self.state = state

    def get_start_point(self):
        return True, self.start[0], self.start[1]

    def get_current_event_state(self):
        return self.state


class Driver:
    def __init__(self, window):
        self.window = window
        self.canvas = window.canvas

    def drag(self, x0, y0, x1, y1, state=0, steps=6):
        """이미지 좌표로 드래그한다."""
        c = self.canvas
        sx0, sy0 = c.to_screen(x0, y0)
        sx1, sy1 = c.to_screen(x1, y1)
        gesture = FakeGesture(sx0, sy0, state)
        c._drag_begin(gesture, sx0, sy0)
        for i in range(1, steps + 1):
            c._drag_update(gesture, (sx1 - sx0) * i / steps, (sy1 - sy0) * i / steps)
        c._drag_end(gesture, sx1 - sx0, sy1 - sy0)

    def key(self, keyval, state=0):
        return self.window._key_pressed(None, keyval, 0, state)


def run_checks(window):
    d = Driver(window)
    c = d.canvas

    # 1) 도구 단축키 + 그리기
    assert d.key(Gdk.KEY_2) and c.tool == "rect"
    d.drag(100, 100, 300, 220)
    assert len(c.shapes) == 1 and c.selected is c.shapes[0]
    box = c.shapes[0]
    assert (round(box.x1), round(box.y1), round(box.x2), round(box.y2)) == (100, 100, 300, 220)

    # 2) Shift = 정사각형
    d.drag(400, 100, 520, 150, state=SHIFT)
    square = c.shapes[1]
    assert abs((square.x2 - square.x1) - (square.y2 - square.y1)) < 0.01

    # 3) 테두리를 잡고 옮기기 (도구를 바꾸지 않아도 된다)
    d.drag(100, 160, 150, 200)
    assert c.selected is box and round(box.x1) == 150 and round(box.y1) == 140, (box.x1, box.y1)

    # 4) 모서리 손잡이로 크기 조절
    d.drag(box.x2, box.y2, 420, 330)
    assert round(box.x2) == 420 and round(box.y2) == 330

    # 5) 색·굵기 바꾸면 선택한 도형에 적용
    window.color_buttons[4][1].set_active(True)
    window.size_buttons[2].set_active(True)
    assert box.color == window.color_buttons[4][0] and box.width == c.stroke_width(2)

    # 6) 화살표 · 동그라미 · 세모 · 펜 · 블러
    for key, kind, args in (
        (Gdk.KEY_a, "arrow", (500, 300, 700, 380)),
        (Gdk.KEY_o, "ellipse", (520, 400, 700, 520)),
        (Gdk.KEY_4, "triangle", (720, 60, 880, 200)),
        (Gdk.KEY_p, "pen", (60, 420, 300, 500)),
        (Gdk.KEY_b, "blur", (600, 220, 900, 290)),
    ):
        assert d.key(key) and c.tool == kind, kind
        before = len(c.shapes)
        d.drag(*args)
        assert len(c.shapes) == before + 1 and c.shapes[-1].kind == kind, kind
    assert c.selected is not None and c.selected.kind == "blur"

    # 7) 너무 작은 드래그는 도형을 만들지 않는다
    d.key(Gdk.KEY_r)
    count = len(c.shapes)
    d.drag(30, 30, 31, 31)
    assert len(c.shapes) == count

    # 8) 텍스트: 클릭 → 입력 → Enter. 조합 중인 한글 마지막 글자도 살아야 한다.
    d.key(Gdk.KEY_t)
    d.drag(120, 520, 120, 520)
    assert c.editing and window._text_view.get_visible()
    window._text_view.get_buffer().set_text("안녕하세")
    window._text_view.emit("preedit-changed", "요")
    assert not d.key(Gdk.KEY_r), "입력 중에는 글자 키가 도구 단축키로 먹히면 안 된다"
    assert d.key(Gdk.KEY_Return)
    assert not c.editing and c.shapes[-1].kind == "text" and c.shapes[-1].text == "안녕하세요", c.shapes[-1].text

    # 8-1) 두 번 클릭하면 그 글자를 다시 편집한다
    label = c.shapes[-1]
    sx, sy = c.to_screen(label.x1 + 10, label.y1 + 10)
    c._clicked(None, 2, sx, sy)
    assert c.editing and c.editing["shape"] is label
    assert window._text_view.get_buffer().props.text == "안녕하세요"
    window._text_view.get_buffer().set_text("고친 글자")
    assert d.key(Gdk.KEY_Return) and label.text == "고친 글자" and len(c.shapes) == 8

    # 9) 실행 취소 / 다시 실행 / 삭제
    total = len(c.shapes)
    assert d.key(Gdk.KEY_z, CTRL) and c.shapes[-1].text == "안녕하세요", "글자 고친 것만 되돌아가야 한다"
    assert d.key(Gdk.KEY_z, CTRL) and len(c.shapes) == total - 1
    assert d.key(Gdk.KEY_z, CTRL | SHIFT) and len(c.shapes) == total
    assert d.key(Gdk.KEY_y, CTRL) and c.shapes[-1].text == "고친 글자"
    c.selected = c.shapes[0]
    assert d.key(Gdk.KEY_Delete) and len(c.shapes) == total - 1
    assert d.key(Gdk.KEY_z, CTRL) and len(c.shapes) == total

    # 10) 자르기: 도구 → 드래그 → Enter
    assert d.key(Gdk.KEY_c) and c.tool == "crop" and window.pill.get_visible_child_name() == "crop"
    d.drag(980, 580, 990, 590)  # 영역 안에서 살짝 끌기 = 이동(가장자리라 그대로)
    c.crop_draft = [0, 0, c.tex_w, c.tex_h]
    c._drag_begin(FakeGesture(*c.to_screen(0, 0)), *c.to_screen(0, 0))  # 왼쪽 위 손잡이
    c._drag_update(FakeGesture(*c.to_screen(0, 0)), 200 * c.transform()[0], 100 * c.transform()[0])
    c._drag_end(None, 0, 0)
    assert [round(v) for v in c.crop_draft] == [200, 100, 800, 500], c.crop_draft
    assert d.key(Gdk.KEY_Return)
    assert c.crop == (200, 100, 800, 500) and c.tool != "crop"
    assert window.pill.get_visible_child_name() == "style"

    # 11) 내보내기 크기 = 자른 크기, Ctrl+C 로 복사
    png = window.copy()
    from gi.repository import GdkPixbuf

    loader = GdkPixbuf.PixbufLoader.new_with_type("png")
    loader.write(png)
    loader.close()
    out = loader.get_pixbuf()
    assert (out.get_width(), out.get_height()) == (800, 500)
    assert not c.dirty

    # 12) Esc: 선택 해제 → (변경 없으면) 닫기
    c.selected = c.shapes[0]
    d.key(Gdk.KEY_Escape)
    assert c.selected is None
    return len(png)


class TestApp(ChalkakApp):
    def __init__(self):
        Adw.Application.__init__(self, application_id="app.chalkak.Test", flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.session = None
        self.last_path = None
        self.ratios = {}
        self.result = 1

    def do_activate(self):
        self.window = EditorWindow(self, make_background(1000, 600), 1.0)
        self.window.present()
        GLib.timeout_add(700, self._run)

    def _run(self):
        try:
            size = run_checks(self.window)
            print(f"OK — 편집 창 동작 13항목 통과 (내보낸 PNG {size} bytes)")
            self.result = 0
        except Exception:  # noqa: BLE001
            import traceback

            traceback.print_exc()
        GLib.timeout_add(300, self._finish)
        return GLib.SOURCE_REMOVE

    def _finish(self):
        self.window.close()
        self.quit()
        return GLib.SOURCE_REMOVE


if __name__ == "__main__":
    app = TestApp()
    app.run([])
    sys.exit(app.result)
