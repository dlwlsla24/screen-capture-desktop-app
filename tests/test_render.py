"""창을 띄우지 않고 편집 캔버스의 그리기·내보내기·실행취소·히트테스트를 검증한다.

실행: /usr/bin/python3 tests/test_render.py [결과 PNG 경로]
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gtk  # noqa: E402

Gtk.init()

from chalkak.canvas import Canvas  # noqa: E402
from chalkak.shapes import Shape, constrain  # noqa: E402


class FakeEditor:
    def sync_actions(self):
        pass

    def sync_style(self, shape):
        pass


def make_background(width=900, height=560):
    """줄무늬 + 글자 비슷한 무늬 — 블러가 실제로 뭉개는지 확인하기 위한 배경."""
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, width, height)
    pixbuf.fill(0xF4F4F6FF)
    for row in range(12):
        for col in range(40):
            if (row * 7 + col * 3) % 5:
                dark = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 12, 14)
                dark.fill(0x22232AFF)
                dark.copy_area(0, 0, 12, 14, pixbuf, 40 + col * 20, 40 + row * 40)
    return pixbuf


def shape(kind, x1, y1, x2, y2, color=(1, 0.23, 0.19), width=4.5):
    s = Shape(kind, color, width)
    s.x1, s.y1, s.x2, s.y2 = x1, y1, x2, y2
    return s


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/chalkak-render.png"
    canvas = Canvas(FakeEditor(), make_background())

    canvas.checkpoint()
    canvas.shapes.append(shape("rect", 60, 40, 300, 160))
    canvas.shapes.append(shape("ellipse", 340, 40, 560, 160, (0.04, 0.52, 1.0)))
    canvas.shapes.append(shape("triangle", 600, 40, 820, 160, (0.2, 0.78, 0.35)))
    canvas.shapes.append(shape("arrow", 80, 330, 360, 220, (1, 0.58, 0), 8.0))
    canvas.shapes.append(shape("arrow", 80, 380, 200, 380, (0.11, 0.11, 0.12), 2.5))
    canvas.shapes.append(shape("blur", 420, 200, 820, 340, width=4.5))
    pen = Shape("pen", (1, 0.84, 0.04), 4.5)
    pen.points = [(80 + i * 12, 470 + (18 if i % 2 else -18)) for i in range(22)]
    canvas.shapes.append(pen)
    text = Shape("text", (1, 0.23, 0.19))
    text.x1, text.y1, text.text, text.size = 430, 400, "여기를 보세요 ABC", 44
    canvas.shapes.append(text)
    white = Shape("text", (1, 1, 1))
    white.x1, white.y1, white.text, white.size = 430, 470, "흰색 글자 + 줄바꿈\n둘째 줄", 28
    canvas.shapes.append(white)

    png = canvas.render_png()
    with open(out, "wb") as handle:
        handle.write(png)
    full = GdkPixbuf.Pixbuf.new_from_file(out)
    assert (full.get_width(), full.get_height()) == (900, 560), "원본 해상도로 내보내야 한다"

    # 히트 테스트: 테두리만 잡히고 안쪽은 통과
    rect_shape = canvas.shapes[0]
    assert rect_shape.hit(60, 100, 6) and not rect_shape.hit(180, 100, 6)
    assert canvas.shapes[1].hit(450, 40, 6) and not canvas.shapes[1].hit(450, 100, 6)
    assert canvas.shapes[3].hit(220, 275, 6)
    assert canvas.shapes[5].hit(600, 270, 0), "블러는 안쪽 전체가 잡혀야 한다"
    assert text.hit(450, 420, 0), "텍스트는 글자 상자 안이 잡혀야 한다"

    # Shift 제약
    assert constrain("rect", 0, 0, 100, 40) == (100, 100)
    ax, ay = constrain("arrow", 0, 0, 100, 8)
    assert abs(ay) < 1e-6 and abs(ax - (100**2 + 8**2) ** 0.5) < 1e-6

    # 자르기 + 실행취소
    canvas.tool = "crop"
    canvas.crop_draft = [400.4, 180.2, 440, 200]
    canvas.apply_crop()
    canvas.tool = "arrow"
    assert canvas.crop == (400, 180, 440, 200)
    cropped_path = out.replace(".png", "-crop.png")
    with open(cropped_path, "wb") as handle:
        handle.write(canvas.render_png())
    cropped = GdkPixbuf.Pixbuf.new_from_file(cropped_path)
    assert (cropped.get_width(), cropped.get_height()) == (440, 200)
    canvas.undo()
    assert canvas.crop == (0, 0, 900, 560) and len(canvas.shapes) == 9
    canvas.undo()
    assert canvas.shapes == []
    canvas.redo()
    assert len(canvas.shapes) == 9
    canvas.selected = canvas.shapes[0]
    assert canvas.delete_selected() and len(canvas.shapes) == 8
    canvas.undo()
    assert len(canvas.shapes) == 9

    # 블러가 실제로 뭉개는가: 블러 영역의 명암 대비가 원본보다 훨씬 작아야 한다.
    def contrast(pixbuf, x, y, w, h):
        data, stride, channels = pixbuf.get_pixels(), pixbuf.get_rowstride(), pixbuf.get_n_channels()
        values = [data[(y + j) * stride + (x + i) * channels] for j in range(0, h, 3) for i in range(0, w, 3)]
        return max(values) - min(values)

    assert contrast(full, 60, 200, 300, 100) > 150, "블러 밖은 선명해야 한다"
    assert contrast(full, 450, 225, 340, 90) < 90, "블러 안은 뭉개져야 한다"
    print("OK", out, cropped_path)


if __name__ == "__main__":
    main()
