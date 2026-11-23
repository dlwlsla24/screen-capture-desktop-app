"""도형을 GTK4(GSK)로 그린다. 도형 모델과 히트 테스트는 core.py 에 있다."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gsk, Pango  # noqa: E402

from .core import BOX_KINDS, Shape, constrain  # noqa: F401  (다른 모듈·테스트가 여기서 가져다 쓴다)
from .util import point, rect, rgba

KAPPA = 0.5522847498


def draw_shape(snapshot, shape, ctx):
    """ctx: .texture(원본) .tex_w .tex_h .pango(Pango.Context)"""
    if shape.kind == "blur":
        _draw_blur(snapshot, shape, ctx)
        return
    color = rgba(*shape.color)
    if shape.kind == "text":
        _draw_text(snapshot, shape, ctx, color)
        return
    stroke = Gsk.Stroke.new(shape.width)
    stroke.set_line_join(Gsk.LineJoin.ROUND)
    stroke.set_line_cap(Gsk.LineCap.ROUND)
    if shape.kind == "arrow":
        _draw_arrow(snapshot, shape, color, stroke)
        return
    builder = Gsk.PathBuilder.new()
    x, y, w, h = shape.bounds()
    if shape.kind == "rect":
        builder.add_rect(rect(x, y, w, h))
    elif shape.kind == "ellipse":
        _add_ellipse(builder, x + w / 2, y + h / 2, w / 2, h / 2)
    elif shape.kind == "triangle":
        a, b, c = shape.triangle()
        builder.move_to(*a)
        builder.line_to(*b)
        builder.line_to(*c)
        builder.close()
    elif shape.kind == "pen":
        _add_smooth(builder, shape.points)
    snapshot.append_stroke(builder.to_path(), stroke, color)


def _draw_arrow(snapshot, shape, color, stroke):
    parts = shape.arrow_parts()
    if parts is None:
        return
    shaft_end, tip_points = parts
    shaft = Gsk.PathBuilder.new()
    shaft.move_to(shape.x1, shape.y1)
    shaft.line_to(*shaft_end)
    snapshot.append_stroke(shaft.to_path(), stroke, color)
    tip = Gsk.PathBuilder.new()
    tip.move_to(*tip_points[0])
    tip.line_to(*tip_points[1])
    tip.line_to(*tip_points[2])
    tip.close()
    path = tip.to_path()
    snapshot.append_fill(path, Gsk.FillRule.WINDING, color)
    soft = Gsk.Stroke.new(max(1.0, shape.width * 0.35))
    soft.set_line_join(Gsk.LineJoin.ROUND)
    snapshot.append_stroke(path, soft, color)


def _draw_blur(snapshot, shape, ctx):
    x, y, w, h = shape.bounds()
    if w < 1 or h < 1:
        return
    radius = shape.blur_radius()
    pad = radius * 4
    snapshot.push_clip(rect(x, y, w, h))
    snapshot.push_blur(radius)
    # 이미지 가장자리에서 투명이 섞여 원본이 비치지 않도록 회색을 먼저 깐다.
    snapshot.append_color(rgba(0.5, 0.5, 0.5, 1), rect(-pad, -pad, ctx.tex_w + 2 * pad, ctx.tex_h + 2 * pad))
    snapshot.append_texture(ctx.texture, rect(0, 0, ctx.tex_w, ctx.tex_h))
    snapshot.pop()
    snapshot.pop()


def text_layout(shape, pango_context):
    """글자 배치를 만들고 shape.text_size 를 갱신한다."""
    layout = Pango.Layout.new(pango_context)
    font = Pango.FontDescription("Sans Bold")
    font.set_absolute_size(shape.size * Pango.SCALE)
    layout.set_font_description(font)
    layout.set_text(shape.text, -1)
    shape.text_size = layout.get_pixel_size()
    return layout


def _draw_text(snapshot, shape, ctx, color):
    layout = text_layout(shape, ctx.pango)
    outline = rgba(0, 0, 0, 0.75) if shape.outline_is_dark() else rgba(1, 1, 1, 0.92)
    snapshot.save()
    snapshot.translate(point(shape.x1, shape.y1))
    builder = Gsk.PathBuilder.new()
    builder.add_layout(layout)
    stroke = Gsk.Stroke.new(max(1.5, shape.size / 9))
    stroke.set_line_join(Gsk.LineJoin.ROUND)
    snapshot.append_stroke(builder.to_path(), stroke, outline)
    snapshot.append_layout(layout, color)
    snapshot.restore()


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
