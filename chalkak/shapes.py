"""주석 도형: 모델 · 그리기(GSK) · 히트 테스트.

좌표는 전부 원본 이미지 픽셀 기준이다. 화면 배율은 캔버스가 변환으로 처리한다.
"""

import copy
import math

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gsk, Pango  # noqa: E402

from .util import point, rect, rgba

BOX_KINDS = ("rect", "ellipse", "triangle", "blur")
KAPPA = 0.5522847498


class Shape:
    def __init__(self, kind, color=(1, 0.23, 0.19), width=4.0):
        self.kind = kind
        self.color = color
        self.width = width
        # 두 점으로 정의되는 도형 (네모·동그라미·세모·블러·화살표)
        self.x1 = self.y1 = self.x2 = self.y2 = 0.0
        # 펜
        self.points = []
        # 텍스트
        self.text = ""
        self.size = 24.0
        self._text_size = (0, 0)

    def copy(self):
        return copy.copy(self) if not self.points else self._deep()

    def _deep(self):
        other = copy.copy(self)
        other.points = list(self.points)
        return other

    # ── 기하 ──────────────────────────────────────────────────────────────
    def bounds(self):
        """(x, y, w, h)"""
        if self.kind == "pen":
            xs = [p[0] for p in self.points] or [0]
            ys = [p[1] for p in self.points] or [0]
            return (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))
        if self.kind == "text":
            return (self.x1, self.y1, self._text_size[0], self._text_size[1])
        x, y = min(self.x1, self.x2), min(self.y1, self.y2)
        return (x, y, abs(self.x2 - self.x1), abs(self.y2 - self.y1))

    def normalize(self):
        if self.kind in BOX_KINDS:
            self.x1, self.x2 = min(self.x1, self.x2), max(self.x1, self.x2)
            self.y1, self.y2 = min(self.y1, self.y2), max(self.y1, self.y2)

    def is_tiny(self, limit=3.0):
        if self.kind == "pen":
            return len(self.points) < 2
        if self.kind == "text":
            return not self.text.strip()
        if self.kind == "arrow":
            return math.hypot(self.x2 - self.x1, self.y2 - self.y1) < limit * 2
        _x, _y, w, h = self.bounds()
        return w < limit or h < limit

    def move(self, dx, dy):
        self.x1 += dx
        self.x2 += dx
        self.y1 += dy
        self.y2 += dy
        if self.points:
            self.points = [(px + dx, py + dy) for px, py in self.points]

    def handles(self):
        """[(이름, x, y)] — 크기 조절 손잡이."""
        if self.kind in BOX_KINDS:
            return [
                ("x1y1", self.x1, self.y1),
                ("x2y1", self.x2, self.y1),
                ("x1y2", self.x1, self.y2),
                ("x2y2", self.x2, self.y2),
            ]
        if self.kind == "arrow":
            return [("x1y1", self.x1, self.y1), ("x2y2", self.x2, self.y2)]
        return []

    def set_handle(self, name, x, y):
        setattr(self, name[:2], x)
        setattr(self, name[2:], y)

    def hit(self, x, y, tol):
        """(x, y) 가 이 도형 위인가. tol 은 이미지 픽셀 단위 허용 오차."""
        reach = tol + self.width / 2
        if self.kind in ("blur", "text"):
            bx, by, bw, bh = self.bounds()
            return bx - tol <= x <= bx + bw + tol and by - tol <= y <= by + bh + tol
        if self.kind == "rect":
            bx, by, bw, bh = self.bounds()
            inside_outer = bx - reach <= x <= bx + bw + reach and by - reach <= y <= by + bh + reach
            inside_inner = bx + reach < x < bx + bw - reach and by + reach < y < by + bh - reach
            return inside_outer and not inside_inner
        if self.kind == "ellipse":
            bx, by, bw, bh = self.bounds()
            rx, ry = bw / 2, bh / 2
            if rx < 1 or ry < 1:
                return False
            d = math.hypot((x - bx - rx) / rx, (y - by - ry) / ry)
            return abs(d - 1) * min(rx, ry) <= reach
        if self.kind == "triangle":
            pts = self._triangle()
            return any(
                _segment_distance(x, y, *pts[i], *pts[(i + 1) % 3]) <= reach for i in range(3)
            )
        if self.kind == "arrow":
            head = self._head_size() * 0.45
            return _segment_distance(x, y, self.x1, self.y1, self.x2, self.y2) <= max(reach, head * 0.6)
        if self.kind == "pen":
            pts = self.points
            return any(
                _segment_distance(x, y, *pts[i], *pts[i + 1]) <= reach for i in range(len(pts) - 1)
            )
        return False

    def _triangle(self):
        x, y, w, h = self.bounds()
        return [(x + w / 2, y), (x + w, y + h), (x, y + h)]

    def _head_size(self):
        length = math.hypot(self.x2 - self.x1, self.y2 - self.y1)
        return min(max(self.width * 4.4, 13.0), max(length * 0.7, 1.0))

    # ── 그리기 ────────────────────────────────────────────────────────────
    def draw(self, snapshot, ctx):
        """ctx: .texture(원본) .tex_w .tex_h .pango(Pango.Context)"""
        if self.kind == "blur":
            self._draw_blur(snapshot, ctx)
            return
        color = rgba(*self.color)
        if self.kind == "text":
            self._draw_text(snapshot, ctx, color)
            return
        stroke = Gsk.Stroke.new(self.width)
        stroke.set_line_join(Gsk.LineJoin.ROUND)
        stroke.set_line_cap(Gsk.LineCap.ROUND)
        builder = Gsk.PathBuilder.new()
        x, y, w, h = self.bounds()
        if self.kind == "rect":
            builder.add_rect(rect(x, y, w, h))
        elif self.kind == "ellipse":
            _add_ellipse(builder, x + w / 2, y + h / 2, w / 2, h / 2)
        elif self.kind == "triangle":
            a, b, c = self._triangle()
            builder.move_to(*a)
            builder.line_to(*b)
            builder.line_to(*c)
            builder.close()
        elif self.kind == "pen":
            _add_smooth(builder, self.points)
        elif self.kind == "arrow":
            self._draw_arrow(snapshot, color, stroke)
            return
        snapshot.append_stroke(builder.to_path(), stroke, color)

    def _draw_arrow(self, snapshot, color, stroke):
        dx, dy = self.x2 - self.x1, self.y2 - self.y1
        length = math.hypot(dx, dy)
        if length < 0.5:
            return
        ux, uy = dx / length, dy / length
        head = self._head_size()
        half = head * 0.45
        bx, by = self.x2 - ux * head, self.y2 - uy * head
        shaft = Gsk.PathBuilder.new()
        shaft.move_to(self.x1, self.y1)
        shaft.line_to(self.x2 - ux * head * 0.8, self.y2 - uy * head * 0.8)
        snapshot.append_stroke(shaft.to_path(), stroke, color)
        tip = Gsk.PathBuilder.new()
        tip.move_to(self.x2, self.y2)
        tip.line_to(bx - uy * half, by + ux * half)
        tip.line_to(bx + uy * half, by - ux * half)
        tip.close()
        path = tip.to_path()
        snapshot.append_fill(path, Gsk.FillRule.WINDING, color)
        soft = Gsk.Stroke.new(max(1.0, self.width * 0.35))
        soft.set_line_join(Gsk.LineJoin.ROUND)
        snapshot.append_stroke(path, soft, color)

    def blur_radius(self):
        return 5.0 + self.width * 2.4

    def _draw_blur(self, snapshot, ctx):
        x, y, w, h = self.bounds()
        if w < 1 or h < 1:
            return
        radius = self.blur_radius()
        pad = radius * 4
        snapshot.push_clip(rect(x, y, w, h))
        snapshot.push_blur(radius)
        # 이미지 가장자리에서 투명이 섞여 원본이 비치지 않도록 회색을 먼저 깐다.
        snapshot.append_color(
            rgba(0.5, 0.5, 0.5, 1), rect(-pad, -pad, ctx.tex_w + 2 * pad, ctx.tex_h + 2 * pad)
        )
        snapshot.append_texture(ctx.texture, rect(0, 0, ctx.tex_w, ctx.tex_h))
        snapshot.pop()
        snapshot.pop()

    def layout(self, ctx):
        layout = Pango.Layout.new(ctx.pango)
        font = Pango.FontDescription("Sans Bold")
        font.set_absolute_size(self.size * Pango.SCALE)
        layout.set_font_description(font)
        layout.set_text(self.text, -1)
        self._text_size = layout.get_pixel_size()
        return layout

    def _draw_text(self, snapshot, ctx, color):
        layout = self.layout(ctx)
        r, g, b = self.color
        luminance = 0.299 * r + 0.587 * g + 0.114 * b
        outline = rgba(0, 0, 0, 0.75) if luminance > 0.6 else rgba(1, 1, 1, 0.92)
        snapshot.save()
        snapshot.translate(point(self.x1, self.y1))
        builder = Gsk.PathBuilder.new()
        builder.add_layout(layout)
        stroke = Gsk.Stroke.new(max(1.5, self.size / 9))
        stroke.set_line_join(Gsk.LineJoin.ROUND)
        snapshot.append_stroke(builder.to_path(), stroke, outline)
        snapshot.append_layout(layout, color)
        snapshot.restore()


# ── 보조 ───────────────────────────────────────────────────────────────────
def _segment_distance(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    span = dx * dx + dy * dy
    if span == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / span))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def _add_ellipse(builder, cx, cy, rx, ry):
    kx, ky = rx * KAPPA, ry * KAPPA
    builder.move_to(cx + rx, cy)
    builder.cubic_to(cx + rx, cy + ky, cx + kx, cy + ry, cx, cy + ry)
    builder.cubic_to(cx - kx, cy + ry, cx - rx, cy + ky, cx - rx, cy)
    builder.cubic_to(cx - rx, cy - ky, cx - kx, cy - ry, cx, cy - ry)
    builder.cubic_to(cx + kx, cy - ry, cx + rx, cy - ky, cx + rx, cy)
    builder.close()


def _add_smooth(builder, pts):
    """점들을 부드러운 곡선으로 잇는다 (중점 2차 베지어)."""
    if not pts:
        return
    builder.move_to(*pts[0])
    if len(pts) == 1:
        builder.line_to(pts[0][0] + 0.01, pts[0][1] + 0.01)
        return
    for i in range(1, len(pts) - 1):
        mx = (pts[i][0] + pts[i + 1][0]) / 2
        my = (pts[i][1] + pts[i + 1][1]) / 2
        builder.quad_to(pts[i][0], pts[i][1], mx, my)
    builder.line_to(*pts[-1])


def constrain(kind, x1, y1, x2, y2):
    """Shift 를 누른 채 그릴 때: 정사각형/정원, 화살표는 45° 단위."""
    dx, dy = x2 - x1, y2 - y1
    if kind == "arrow":
        length = math.hypot(dx, dy)
        angle = round(math.atan2(dy, dx) / (math.pi / 4)) * (math.pi / 4)
        return x1 + math.cos(angle) * length, y1 + math.sin(angle) * length
    side = max(abs(dx), abs(dy))
    return x1 + math.copysign(side, dx or 1), y1 + math.copysign(side, dy or 1)
