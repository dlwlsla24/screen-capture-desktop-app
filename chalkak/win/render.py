"""Pillow 로 그리기 (Windows 판). 화면 표시와 내보내기가 같은 함수를 쓴다.

선은 도형마다 작은 레이어에 3배 크기로 그린 뒤 줄여서 계단 현상을 없앤다.
"""

import math
import os
import sys
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\malgunbd.ttf",  # 맑은 고딕 Bold
    r"C:\Windows\Fonts\malgun.ttf",
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",  # 리눅스에서 테스트할 때
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]
UI_FONT = "Malgun Gothic" if sys.platform == "win32" else "Noto Sans CJK KR"
MAX_LAYER_PIXELS = 12_000_000


@lru_cache(maxsize=1)
def font_path():
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            return path
    return None


@lru_cache(maxsize=64)
def get_font(size):
    size = max(1, round(size))
    path = font_path()
    if path:
        return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


def rgb(color):
    return tuple(max(0, min(255, round(c * 255))) for c in color)


# ── 글자 ───────────────────────────────────────────────────────────────────
def _outline_width(size):
    return max(1, round(max(1.5, size / 9) / 2))


def measure_text(shape):
    """shape.text_size 를 이미지 픽셀 단위로 채운다."""
    draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    font = get_font(shape.size)
    stroke = _outline_width(shape.size)
    box = draw.multiline_textbbox(
        (stroke, stroke), shape.text or " ", font=font, stroke_width=stroke, spacing=shape.size * 0.2
    )
    shape.text_size = (box[2] + stroke, box[3] + stroke)
    return shape.text_size


def _draw_text(image, shape, scale, origin):
    size = shape.size * scale
    stroke = _outline_width(size)
    x = (shape.x1 - origin[0]) * scale + stroke
    y = (shape.y1 - origin[1]) * scale + stroke
    ImageDraw.Draw(image).multiline_text(
        (x, y),
        shape.text,
        font=get_font(size),
        fill=rgb(shape.color),
        stroke_width=stroke,
        stroke_fill=(20, 20, 20) if shape.outline_is_dark() else (255, 255, 255),
        spacing=size * 0.2,
    )


# ── 선·도형 ────────────────────────────────────────────────────────────────
def _smooth_points(points, steps=6):
    """펜 궤적을 부드러운 곡선(중점 2차 베지어)으로 바꾼 점 목록."""
    if len(points) < 3:
        return list(points)
    out = [points[0]]
    start = points[0]
    for i in range(1, len(points) - 1):
        ctrl = points[i]
        end = ((points[i][0] + points[i + 1][0]) / 2, (points[i][1] + points[i + 1][1]) / 2)
        for s in range(1, steps + 1):
            t = s / steps
            a, b, c = (1 - t) ** 2, 2 * (1 - t) * t, t * t
            out.append((a * start[0] + b * ctrl[0] + c * end[0], a * start[1] + b * ctrl[1] + c * end[1]))
        start = end
    out.append(points[-1])
    return out


def _polyline(draw, pts, color, width, closed=False):
    width = max(1, round(width))
    if closed:
        pts = list(pts) + [pts[0], pts[1]]
    draw.line(pts, fill=color, width=width, joint="curve")
    if not closed:  # 둥근 끝
        r = width / 2
        for x, y in (pts[0], pts[-1]):
            draw.ellipse([x - r, y - r, x + r, y + r], fill=color)


def _draw_stroked(image, shape, scale, origin, quality):
    color = rgb(shape.color) + (255,)
    width = max(1.0, shape.width * scale)

    def to_out(x, y):
        return ((x - origin[0]) * scale, (y - origin[1]) * scale)

    if shape.kind == "pen":
        outline = [to_out(*p) for p in _smooth_points(shape.points)]
    elif shape.kind == "arrow":
        outline = [to_out(shape.x1, shape.y1), to_out(shape.x2, shape.y2)]
    else:
        bx, by, bw, bh = shape.bounds()
        outline = [to_out(bx, by), to_out(bx + bw, by + bh)]
    pad = width + 6 + (shape.head_size() * scale if shape.kind == "arrow" else 0)
    x0 = max(0, math.floor(min(p[0] for p in outline) - pad))
    y0 = max(0, math.floor(min(p[1] for p in outline) - pad))
    x1 = min(image.width, math.ceil(max(p[0] for p in outline) + pad))
    y1 = min(image.height, math.ceil(max(p[1] for p in outline) + pad))
    if x1 <= x0 or y1 <= y0:
        return
    w, h = x1 - x0, y1 - y0
    ss = quality
    while ss > 1 and w * h * ss * ss > MAX_LAYER_PIXELS:
        ss -= 1
    layer = Image.new("RGBA", (w * ss, h * ss), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    def to_layer(x, y):
        ox, oy = to_out(x, y)
        return ((ox - x0) * ss, (oy - y0) * ss)

    lw = width * ss
    if shape.kind == "rect":
        bx, by, bw, bh = shape.bounds()
        corners = [to_layer(bx, by), to_layer(bx + bw, by), to_layer(bx + bw, by + bh), to_layer(bx, by + bh)]
        _polyline(draw, corners, color, lw, closed=True)
    elif shape.kind == "ellipse":
        bx, by, bw, bh = shape.bounds()
        (ex0, ey0), (ex1, ey1) = to_layer(bx, by), to_layer(bx + bw, by + bh)
        half = lw / 2  # Pillow 는 테두리를 안쪽으로 그리므로 반만큼 넓혀 선 중심을 맞춘다
        if ex1 - ex0 + lw >= 2 and ey1 - ey0 + lw >= 2:
            draw.ellipse([ex0 - half, ey0 - half, ex1 + half, ey1 + half], outline=color, width=max(1, round(lw)))
    elif shape.kind == "triangle":
        _polyline(draw, [to_layer(*p) for p in shape.triangle()], color, lw, closed=True)
    elif shape.kind == "pen":
        pts = [to_layer(*p) for p in _smooth_points(shape.points)]
        if len(pts) == 1:
            pts = pts * 2
        _polyline(draw, pts, color, lw)
    elif shape.kind == "arrow":
        parts = shape.arrow_parts()
        if parts is not None:
            shaft_end, tip = parts
            _polyline(draw, [to_layer(shape.x1, shape.y1), to_layer(*shaft_end)], color, lw)
            tip = [to_layer(*p) for p in tip]
            draw.polygon(tip, fill=color)
            _polyline(draw, tip, color, max(1.0, lw * 0.35), closed=True)
    if ss > 1:
        layer = layer.resize((w, h), Image.BOX)
    image.paste(layer, (x0, y0), layer)


def _draw_blur(image, shape, scale, origin):
    bx, by, bw, bh = shape.bounds()
    x0 = max(0, round((bx - origin[0]) * scale))
    y0 = max(0, round((by - origin[1]) * scale))
    x1 = min(image.width, round((bx + bw - origin[0]) * scale))
    y1 = min(image.height, round((by + bh - origin[1]) * scale))
    if x1 - x0 < 2 or y1 - y0 < 2:
        return
    region = image.crop((x0, y0, x1, y1)).filter(ImageFilter.GaussianBlur(max(1.0, shape.blur_radius() * scale)))
    image.paste(region, (x0, y0))


def scale_base(base, view, scale):
    """보이는 영역만 잘라 화면 배율로 맞춘 바탕 그림."""
    vx, vy, vw, vh = (round(v) for v in view)
    out = (max(1, round(vw * scale)), max(1, round(vh * scale)))
    image = base.crop((vx, vy, vx + vw, vy + vh))
    if image.size != out:
        image = image.resize(out, Image.LANCZOS if scale < 1 else Image.NEAREST)
    return image


def render_scene(base, items, view, scale=1.0, quality=3, scaled_base=None):
    """바탕 + 도형을 그린 RGB 이미지. items 는 EditState.scene_items() 의 순서."""
    image = scaled_base.copy() if scaled_base is not None else scale_base(base, view, scale)
    origin = (view[0], view[1])
    for shape in items:
        if shape.kind == "blur":
            _draw_blur(image, shape, scale, origin)
        elif shape.kind == "text":
            _draw_text(image, shape, scale, origin)
        else:
            _draw_stroked(image, shape, scale, origin, quality)
    return image


# ── 아이콘 ─────────────────────────────────────────────────────────────────
def tool_icon(name, color=(235, 235, 240), size=20, ss=4):
    """도구 막대 아이콘 (리눅스 판 icons.py 와 같은 그림)."""
    layer = Image.new("RGBA", (20 * ss, 20 * ss), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    color = tuple(color) + (255,)
    lw = 1.8 * ss

    def pts(*points):
        return [(x * ss, y * ss) for x, y in points]

    if name == "arrow":
        _polyline(draw, pts((4.5, 15.5), (13, 7)), color, lw)
        draw.polygon(pts((16.5, 3.5), (15.2, 10.2), (9.8, 4.8)), fill=color)
    elif name == "rect":
        _polyline(draw, pts((3.5, 5), (16.5, 5), (16.5, 15), (3.5, 15)), color, lw, closed=True)
    elif name == "ellipse":
        draw.ellipse([3.4 * ss - lw / 2, 3.4 * ss - lw / 2, 16.6 * ss + lw / 2, 16.6 * ss + lw / 2], outline=color, width=round(lw))
    elif name == "triangle":
        _polyline(draw, pts((10, 4), (16.8, 15.5), (3.2, 15.5)), color, lw, closed=True)
    elif name == "pen":
        curve = []
        for (p0, p1, p2, p3) in (((3, 14.5), (5, 6), (8, 5), (9.5, 9.5)), ((9.5, 9.5), (11, 14), (14, 15), (17, 6))):
            for s in range(13):
                t = s / 12
                a, b, c, d = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t * t, t**3
                curve.append((a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0], a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1]))
        _polyline(draw, pts(*curve), color, lw)
    elif name == "text":
        _polyline(draw, pts((4.5, 7), (4.5, 4.5), (15.5, 4.5), (15.5, 7)), color, 1.9 * ss)
        _polyline(draw, pts((10, 4.5), (10, 15.5)), color, 1.9 * ss)
        _polyline(draw, pts((7.6, 15.5), (12.4, 15.5)), color, 1.9 * ss)
    elif name == "blur":
        faint = color[:3] + (97,)
        for row in range(3):
            for col in range(3):
                x, y = (3.5 + col * 4.5) * ss, (3.5 + row * 4.5) * ss
                draw.rectangle([x, y, x + 3.9 * ss, y + 3.9 * ss], fill=color if (row + col) % 2 == 0 else faint)
    elif name == "crop":
        _polyline(draw, pts((6, 2.5), (6, 14), (17.5, 14)), color, lw)
        _polyline(draw, pts((2.5, 6), (14, 6), (14, 17.5)), color, lw)
    elif name == "undo" or name == "redo":
        arc = [(6, 8), (12, 8), (14.5, 10.5), (12, 13.5), (8, 13.5)]
        head = [(8.2, 5), (5, 8), (8.2, 11)]
        if name == "redo":
            arc = [(20 - x, y) for x, y in arc]
            head = [(20 - x, y) for x, y in head]
        _polyline(draw, pts(*arc), color, lw)
        _polyline(draw, pts(*head), color, lw)
    elif name == "save":
        _polyline(draw, pts((10, 3.5), (10, 12.5)), color, lw)
        _polyline(draw, pts((6, 9), (10, 13), (14, 9)), color, lw)
        _polyline(draw, pts((4, 16.5), (16, 16.5)), color, lw)
    return layer.resize((size, size), Image.LANCZOS)


def app_icon(size=64):
    """앱 아이콘 (트레이·실행 파일용)."""
    ss = 4
    full = 128 * ss
    gradient = Image.new("RGB", (full, full))
    px = gradient.load()
    for y in range(full):
        for x in range(0, full, ss):
            t = (x + y) / (2 * full)
            c = (round(255 + (229 - 255) * t), round(122 + (56 - 122) * t), round(89 + (79 - 89) * t))
            for i in range(ss):
                px[x + i, y] = c
    mask = Image.new("L", (full, full), 0)
    ImageDraw.Draw(mask).rounded_rectangle([8 * ss, 8 * ss, 120 * ss, 120 * ss], radius=26 * ss, fill=255)
    icon = Image.new("RGBA", (full, full), (0, 0, 0, 0))
    icon.paste(gradient, (0, 0), mask)
    draw = ImageDraw.Draw(icon)
    white = (255, 255, 255, 255)
    for corner in (
        [(34, 54), (34, 36), (36, 34), (54, 34)],
        [(74, 34), (92, 34), (94, 36), (94, 54)],
        [(94, 74), (94, 92), (92, 94), (74, 94)],
        [(54, 94), (36, 94), (34, 92), (34, 74)],
    ):
        _polyline(draw, [(x * ss, y * ss) for x, y in corner], white, 8 * ss)
    draw.ellipse([53 * ss, 53 * ss, 75 * ss, 75 * ss], fill=white)
    return icon.resize((size, size), Image.LANCZOS)
