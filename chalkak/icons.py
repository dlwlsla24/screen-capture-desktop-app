"""도구 아이콘 — SVG 없이 코드로 직접 그린다 (테마 색을 그대로 따라간다)."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")
from gi.repository import Gsk, Gtk  # noqa: E402

from .util import point, rect, rgba


def _stroke(width=1.8):
    stroke = Gsk.Stroke.new(width)
    stroke.set_line_join(Gsk.LineJoin.ROUND)
    stroke.set_line_cap(Gsk.LineCap.ROUND)
    return stroke


def _line(snapshot, color, points, close=False, width=1.8):
    builder = Gsk.PathBuilder.new()
    builder.move_to(*points[0])
    for p in points[1:]:
        builder.line_to(*p)
    if close:
        builder.close()
    snapshot.append_stroke(builder.to_path(), _stroke(width), color)


def _fill(snapshot, color, points):
    builder = Gsk.PathBuilder.new()
    builder.move_to(*points[0])
    for p in points[1:]:
        builder.line_to(*p)
    builder.close()
    snapshot.append_fill(builder.to_path(), Gsk.FillRule.WINDING, color)


def _circle(builder, cx, cy, r):
    k = r * 0.5522847498
    builder.move_to(cx + r, cy)
    builder.cubic_to(cx + r, cy + k, cx + k, cy + r, cx, cy + r)
    builder.cubic_to(cx - k, cy + r, cx - r, cy + k, cx - r, cy)
    builder.cubic_to(cx - r, cy - k, cx - k, cy - r, cx, cy - r)
    builder.cubic_to(cx + k, cy - r, cx + r, cy - k, cx + r, cy)
    builder.close()


def draw_arrow(s, c):
    _line(s, c, [(4.5, 15.5), (13, 7)])
    _fill(s, c, [(16.5, 3.5), (15.2, 10.2), (9.8, 4.8)])


def draw_rect(s, c):
    _line(s, c, [(3.5, 5), (16.5, 5), (16.5, 15), (3.5, 15)], close=True)


def draw_ellipse(s, c):
    builder = Gsk.PathBuilder.new()
    _circle(builder, 10, 10, 6.6)
    s.append_stroke(builder.to_path(), _stroke(), c)


def draw_triangle(s, c):
    _line(s, c, [(10, 4), (16.8, 15.5), (3.2, 15.5)], close=True)


def draw_pen(s, c):
    builder = Gsk.PathBuilder.new()
    builder.move_to(3, 14.5)
    builder.cubic_to(5, 6, 8, 5, 9.5, 9.5)
    builder.cubic_to(11, 14, 14, 15, 17, 6)
    s.append_stroke(builder.to_path(), _stroke(), c)


def draw_text(s, c):
    _line(s, c, [(4.5, 7), (4.5, 4.5), (15.5, 4.5), (15.5, 7)], width=1.9)
    _line(s, c, [(10, 4.5), (10, 15.5)], width=1.9)
    _line(s, c, [(7.6, 15.5), (12.4, 15.5)], width=1.9)


def draw_blur(s, c):
    faint = rgba(c.red, c.green, c.blue, c.alpha * 0.38)
    for row in range(3):
        for col in range(3):
            color = c if (row + col) % 2 == 0 else faint
            s.append_color(color, rect(3.5 + col * 4.5, 3.5 + row * 4.5, 3.9, 3.9))


def draw_crop(s, c):
    _line(s, c, [(6, 2.5), (6, 14), (17.5, 14)])
    _line(s, c, [(2.5, 6), (14, 6), (14, 17.5)])


DRAWERS = {
    "arrow": draw_arrow,
    "rect": draw_rect,
    "ellipse": draw_ellipse,
    "triangle": draw_triangle,
    "pen": draw_pen,
    "text": draw_text,
    "blur": draw_blur,
    "crop": draw_crop,
}


class Icon(Gtk.Widget):
    """20×20 도구 아이콘."""

    def __init__(self, name):
        super().__init__()
        self._draw = DRAWERS[name]

    def do_measure(self, _orientation, _for_size):
        return (20, 20, -1, -1)

    def do_snapshot(self, snapshot):
        snapshot.translate(point((self.get_width() - 20) / 2, (self.get_height() - 20) / 2))
        self._draw(snapshot, self.get_color())


class Dot(Gtk.Widget):
    """색 견본 또는 굵기 표시용 원."""

    def __init__(self, color=None, radius=8.0):
        super().__init__()
        self.color = color  # None 이면 글자색(굵기 버튼)
        self.radius = radius

    def do_measure(self, _orientation, _for_size):
        return (20, 20, -1, -1)

    def do_snapshot(self, snapshot):
        w, h = self.get_width(), self.get_height()
        builder = Gsk.PathBuilder.new()
        _circle(builder, w / 2, h / 2, self.radius)
        path = builder.to_path()
        if self.color is None:
            snapshot.append_fill(path, Gsk.FillRule.WINDING, self.get_color())
            return
        snapshot.append_fill(path, Gsk.FillRule.WINDING, rgba(*self.color))
        # 어두운 배경에서 검정, 밝은 배경에서 흰색도 보이도록 얇은 테두리
        snapshot.append_stroke(path, Gsk.Stroke.new(1.0), rgba(1, 1, 1, 0.45))
