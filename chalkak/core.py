"""화면 툴킷과 무관한 편집 로직. 리눅스(GTK)와 Windows(tkinter) 편집 창이 함께 쓴다.

- Shape      도형 한 개 (좌표·히트 테스트·손잡이). 좌표는 전부 원본 이미지 픽셀.
- EditState  편집 상태와 마우스 처리(그리기·옮기기·크기조절·자르기·실행취소)를 담은 믹스인.

이 파일은 표준 라이브러리만 쓴다. 그리기는 각 플랫폼 쪽에서 한다.
"""

import copy
import math

BOX_KINDS = ("rect", "ellipse", "triangle", "blur")

PAD = 28
PAD_TOP = 24
PAD_BOTTOM = 84  # 아래쪽 색·굵기 막대 자리
HANDLE_HIT = 11
MIN_CROP = 12

STROKE_SIZES = (2.5, 4.5, 8.0)
TEXT_SIZES = (18.0, 28.0, 44.0)

CROP_HANDLES = {
    "nw": (0, 0), "n": (0.5, 0), "ne": (1, 0),
    "w": (0, 0.5), "e": (1, 0.5),
    "sw": (0, 1), "s": (0.5, 1), "se": (1, 1),
}  # fmt: skip
CROP_CURSORS = {
    "nw": "nwse-resize", "se": "nwse-resize", "ne": "nesw-resize", "sw": "nesw-resize",
    "n": "ns-resize", "s": "ns-resize", "w": "ew-resize", "e": "ew-resize",
}  # fmt: skip


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
        self.text_size = (0, 0)  # 그려 본 뒤의 글자 상자 크기 (각 플랫폼이 채운다)

    def copy(self):
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
            return (self.x1, self.y1, self.text_size[0], self.text_size[1])
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
            pts = self.triangle()
            return any(segment_distance(x, y, *pts[i], *pts[(i + 1) % 3]) <= reach for i in range(3))
        if self.kind == "arrow":
            head = self.head_size() * 0.45
            return segment_distance(x, y, self.x1, self.y1, self.x2, self.y2) <= max(reach, head * 0.6)
        if self.kind == "pen":
            pts = self.points
            return any(segment_distance(x, y, *pts[i], *pts[i + 1]) <= reach for i in range(len(pts) - 1))
        return False

    def triangle(self):
        x, y, w, h = self.bounds()
        return [(x + w / 2, y), (x + w, y + h), (x, y + h)]

    def head_size(self):
        """화살촉 길이."""
        length = math.hypot(self.x2 - self.x1, self.y2 - self.y1)
        return min(max(self.width * 4.4, 13.0), max(length * 0.7, 1.0))

    def arrow_parts(self):
        """(몸통 끝점, 화살촉 세 점). 길이가 0 이면 None."""
        dx, dy = self.x2 - self.x1, self.y2 - self.y1
        length = math.hypot(dx, dy)
        if length < 0.5:
            return None
        ux, uy = dx / length, dy / length
        head = self.head_size()
        half = head * 0.45
        bx, by = self.x2 - ux * head, self.y2 - uy * head
        shaft_end = (self.x2 - ux * head * 0.8, self.y2 - uy * head * 0.8)
        tip = [(self.x2, self.y2), (bx - uy * half, by + ux * half), (bx + uy * half, by - ux * half)]
        return shaft_end, tip

    def blur_radius(self):
        return 5.0 + self.width * 2.4

    def outline_is_dark(self):
        """글자 테두리를 어둡게 할지(밝은 글자색이면 True)."""
        r, g, b = self.color
        return 0.299 * r + 0.587 * g + 0.114 * b > 0.6


def segment_distance(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    span = dx * dx + dy * dy
    if span == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / span))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def constrain(kind, x1, y1, x2, y2):
    """Shift 를 누른 채 그릴 때: 정사각형/정원, 화살표는 45° 단위."""
    dx, dy = x2 - x1, y2 - y1
    if kind == "arrow":
        length = math.hypot(dx, dy)
        angle = round(math.atan2(dy, dx) / (math.pi / 4)) * (math.pi / 4)
        return x1 + math.cos(angle) * length, y1 + math.sin(angle) * length
    side = max(abs(dx), abs(dy))
    return x1 + math.copysign(side, dx or 1), y1 + math.copysign(side, dy or 1)


class EditState:
    """편집 상태 + 마우스 처리. 화면 위젯 클래스가 함께 상속해서 쓴다.

    위젯 쪽이 제공해야 하는 것:
      get_width(), get_height()          그리는 영역 크기 (화면 픽셀)
      queue_draw()                       다시 그리기 요청
      grab_focus()                       키보드 포커스 가져오기
      set_cursor_from_name(name)         마우스 커서 바꾸기 (CSS 커서 이름)
      measure_text(shape)                shape.text_size 를 채운다
      self.editor                        창 객체: sync_actions(), sync_style(shape),
                                         show_text_entry(editing), hide_text_entry() -> str,
                                         style_text_entry(editing)
    """

    def init_edit_state(self, width, height, ratio=1.0):
        self.tex_w = width
        self.tex_h = height
        self.ratio = max(ratio, 0.1)  # 이미지 픽셀 / 화면 논리 픽셀
        self.shapes = []
        self.crop = (0, 0, width, height)
        self.selected = None
        self.tool = "arrow"
        self.color = (1.0, 0.23, 0.19)
        self.size_idx = 1
        self.undo_stack = []
        self.redo_stack = []
        self.dirty = False
        self.draft = None  # 그리는 중인 도형
        self.drag = None  # 진행 중인 드래그 정보
        self.crop_draft = None  # 자르기 도구에서 조정 중인 영역 [x, y, w, h]
        self.editing = None  # 텍스트 입력 중 정보

    # ── 화면 ↔ 이미지 좌표 ────────────────────────────────────────────────
    def view_rect(self):
        if self.tool == "crop":
            return (0, 0, self.tex_w, self.tex_h)
        return self.crop

    def transform(self):
        """(배율, x 오프셋, y 오프셋). 화면 = 이미지 × 배율 + 오프셋."""
        vx, vy, vw, vh = self.view_rect()
        avail_w = max(self.get_width() - 2 * PAD, 10)
        avail_h = max(self.get_height() - PAD_TOP - PAD_BOTTOM, 10)
        fit = min(avail_w / vw, avail_h / vh)
        native = 1 / self.ratio  # 화면 픽셀과 1:1
        if fit < native:
            k = fit
        elif fit >= native * 2:
            k = native * 2  # 아주 작은 캡처는 2배로 키워 보여 준다
        else:
            k = native
        ox = PAD + (avail_w - vw * k) / 2 - vx * k
        oy = PAD_TOP + (avail_h - vh * k) / 2 - vy * k
        return k, round(ox), round(oy)

    def to_image(self, sx, sy):
        k, ox, oy = self.transform()
        return (sx - ox) / k, (sy - oy) / k

    def to_screen(self, ix, iy):
        k, ox, oy = self.transform()
        return ix * k + ox, iy * k + oy

    def _clamp(self, ix, iy):
        return max(0.0, min(float(self.tex_w), ix)), max(0.0, min(float(self.tex_h), iy))

    # ── 현재 스타일 ───────────────────────────────────────────────────────
    def stroke_width(self, idx=None):
        return STROKE_SIZES[self.size_idx if idx is None else idx] * self.ratio

    def text_size(self, idx=None):
        return TEXT_SIZES[self.size_idx if idx is None else idx] * self.ratio

    def set_color(self, color):
        self.color = color
        if self.editing:
            self.editing["color"] = color
            self.editor.style_text_entry(self.editing)
        shape = self.selected
        if shape and shape.kind != "blur" and shape.color != color:
            self.checkpoint()
            shape.color = color
        self.queue_draw()

    def set_size(self, idx):
        self.size_idx = idx
        if self.editing:
            self.editing["size"] = self.text_size()
            self.editor.style_text_entry(self.editing)
        shape = self.selected
        if shape:
            self.checkpoint()
            if shape.kind == "text":
                shape.size = self.text_size()
                self.measure_text(shape)
            else:
                shape.width = self.stroke_width()
        self.queue_draw()

    def set_tool(self, tool):
        if self.editing:
            self.commit_text()
        self.tool = tool
        self.draft = None
        self.drag = None
        if tool == "crop":
            self.selected = None
            self.crop_draft = list(self.crop)
        else:
            self.crop_draft = None
        self.set_cursor_from_name("text" if tool == "text" else "crosshair")
        self.queue_draw()

    # ── 실행 취소 ─────────────────────────────────────────────────────────
    def state(self):
        return ([s.copy() for s in self.shapes], self.crop)

    def checkpoint(self, before=None):
        """바꾸기 '직전' 상태를 실행취소 목록에 넣는다."""
        self.undo_stack.append(before or self.state())
        del self.undo_stack[:-100]
        self.redo_stack.clear()
        self.dirty = True
        self.editor.sync_actions()

    def _restore(self, state):
        selected_index = self.shapes.index(self.selected) if self.selected in self.shapes else -1
        self.shapes = [s.copy() for s in state[0]]
        self.crop = state[1]
        self.selected = self.shapes[selected_index] if 0 <= selected_index < len(self.shapes) else None
        if self.tool == "crop":
            self.crop_draft = list(self.crop)
        self.dirty = True
        self.editor.sync_actions()
        self.queue_draw()

    def undo(self):
        if self.editing:
            self.commit_text()
        if self.undo_stack:
            self.redo_stack.append(self.state())
            self._restore(self.undo_stack.pop())

    def redo(self):
        if self.redo_stack:
            self.undo_stack.append(self.state())
            self._restore(self.redo_stack.pop())

    def delete_selected(self):
        if self.selected in self.shapes:
            self.checkpoint()
            self.shapes.remove(self.selected)
            self.selected = None
            self.queue_draw()
            return True
        return False

    def deselect(self):
        if self.selected is not None:
            self.selected = None
            self.queue_draw()
            return True
        return False

    def scene_items(self, final=False):
        """그릴 순서대로: 블러 먼저, 그 위에 나머지. (입력 중인 글자는 화면에서 숨긴다.)"""
        items = list(self.shapes)
        if self.draft is not None and not final:
            items.append(self.draft)
        hidden = self.editing["shape"] if self.editing and not final else None
        blurs = [s for s in items if s.kind == "blur"]
        others = [s for s in items if s.kind != "blur" and s is not hidden]
        return blurs + others

    # ── 히트 테스트 ───────────────────────────────────────────────────────
    def shape_at(self, ix, iy):
        k, _ox, _oy = self.transform()
        tol = 7 / k
        for shape in reversed(self.shapes):
            if shape.kind != "blur" and shape.hit(ix, iy, tol):
                return shape
        for shape in reversed(self.shapes):
            if shape.kind == "blur" and shape.hit(ix, iy, 0):
                return shape
        return None

    def _handle_at(self, sx, sy):
        if self.selected is None:
            return None
        for name, hx, hy in self.selected.handles():
            px, py = self.to_screen(hx, hy)
            if math.hypot(sx - px, sy - py) <= HANDLE_HIT:
                return name
        return None

    def _crop_handle_at(self, sx, sy):
        cx, cy, cw, ch = self.crop_draft
        x, y = self.to_screen(cx, cy)
        k, _ox, _oy = self.transform()
        for name, (fx, fy) in CROP_HANDLES.items():
            if math.hypot(sx - (x + cw * k * fx), sy - (y + ch * k * fy)) <= HANDLE_HIT:
                return name
        return None

    def _in_crop(self, ix, iy):
        cx, cy, cw, ch = self.crop_draft
        return cx <= ix <= cx + cw and cy <= iy <= cy + ch

    # ── 마우스 (좌표는 위젯 기준 화면 픽셀) ───────────────────────────────
    def pointer_motion(self, x, y):
        """드래그 중이 아닐 때 커서 모양을 정한다."""
        if self.drag:
            return
        ix, iy = self.to_image(x, y)
        cursor = "text" if self.tool == "text" else "crosshair"
        if self.tool == "crop":
            handle = self._crop_handle_at(x, y)
            if handle:
                cursor = CROP_CURSORS[handle]
            elif self._in_crop(ix, iy):
                cursor = "move"
        elif self._handle_at(x, y):
            cursor = "pointer"
        elif self.tool != "pen" and self.shape_at(ix, iy) is not None:
            cursor = "move"
        self.set_cursor_from_name(cursor)

    def pointer_press(self, x, y, shift=False):
        self.grab_focus()
        self.drag = None
        if self.editing:
            self.commit_text()
            return
        ix, iy = self.to_image(x, y)

        if self.tool == "crop":
            handle = self._crop_handle_at(x, y)
            if handle:
                self.drag = {"mode": "crop-handle", "handle": handle}
            elif self._in_crop(ix, iy):
                self.drag = {"mode": "crop-move", "start": (ix, iy), "orig": list(self.crop_draft)}
            else:
                self.drag = {"mode": "crop-new", "start": self._clamp(ix, iy)}
            return

        handle = self._handle_at(x, y)
        if handle:
            self.drag = {"mode": "handle", "name": handle, "before": self.state(), "changed": False}
            return

        hit = self.shape_at(ix, iy) if self.tool != "pen" else None
        if hit is not None:
            self.selected = hit
            self.editor.sync_style(hit)
            self.drag = {"mode": "move", "last": (ix, iy), "before": self.state(), "changed": False}
            self.queue_draw()
            return

        self.selected = None
        if self.tool == "text":
            self.drag = {"mode": "text", "at": (ix, iy)}
            self.queue_draw()
            return

        cx, cy = self._clamp(ix, iy)
        shape = Shape(self.tool, self.color, self.stroke_width())
        shape.x1 = shape.x2 = cx
        shape.y1 = shape.y2 = cy
        if self.tool == "pen":
            shape.points = [(cx, cy)]
        self.draft = shape
        self.drag = {"mode": "create", "shift": shift}
        self.queue_draw()

    def pointer_drag(self, x, y, shift=False):
        drag = self.drag
        if not drag:
            return
        ix, iy = self.to_image(x, y)
        mode = drag["mode"]

        if mode == "create":
            shape = self.draft
            cx, cy = self._clamp(ix, iy)
            if shape.kind == "pen":
                lx, ly = shape.points[-1]
                k, _ox, _oy = self.transform()
                if math.hypot(cx - lx, cy - ly) >= 1.5 / k:
                    shape.points.append((cx, cy))
            else:
                if shift:
                    cx, cy = constrain(shape.kind, shape.x1, shape.y1, cx, cy)
                shape.x2, shape.y2 = cx, cy
        elif mode == "handle":
            shape = self.selected
            if shape.kind in BOX_KINDS:
                ix, iy = self._clamp(ix, iy)
            shape.set_handle(drag["name"], ix, iy)
            drag["changed"] = True
        elif mode == "move":
            lx, ly = drag["last"]
            if (ix, iy) != (lx, ly):
                self.selected.move(ix - lx, iy - ly)
                drag["last"] = (ix, iy)
                drag["changed"] = True
        elif mode == "crop-new":
            x0, y0 = drag["start"]
            x1, y1 = self._clamp(ix, iy)
            if abs(x1 - x0) >= MIN_CROP and abs(y1 - y0) >= MIN_CROP:
                self.crop_draft = [min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)]
        elif mode == "crop-move":
            ox, oy, ow, oh = drag["orig"]
            sx0, sy0 = drag["start"]
            nx = max(0, min(self.tex_w - ow, ox + ix - sx0))
            ny = max(0, min(self.tex_h - oh, oy + iy - sy0))
            self.crop_draft = [nx, ny, ow, oh]
        elif mode == "crop-handle":
            self._resize_crop(drag["handle"], *self._clamp(ix, iy))
        self.queue_draw()

    def _resize_crop(self, handle, ix, iy):
        x, y, w, h = self.crop_draft
        left, top, right, bottom = x, y, x + w, y + h
        if "w" in handle:
            left = min(ix, right - MIN_CROP)
        if "e" in handle:
            right = max(ix, left + MIN_CROP)
        if "n" in handle:
            top = min(iy, bottom - MIN_CROP)
        if "s" in handle:
            bottom = max(iy, top + MIN_CROP)
        self.crop_draft = [left, top, right - left, bottom - top]

    def pointer_release(self, dx=0.0, dy=0.0):
        """dx, dy: 누른 곳에서 놓은 곳까지의 화면 거리."""
        drag, self.drag = self.drag, None
        if not drag:
            return
        mode = drag["mode"]
        if mode == "create":
            shape, self.draft = self.draft, None
            shape.normalize()
            if not shape.is_tiny(3 * self.ratio):
                self.checkpoint()
                self.shapes.append(shape)
                if shape.kind != "pen":
                    self.selected = shape
        elif mode in ("handle", "move"):
            if drag["changed"]:
                self.selected.normalize()
                self.checkpoint(drag["before"])
        elif mode == "text":
            if math.hypot(dx, dy) < 8:
                self.start_text(*drag["at"])
        self.queue_draw()

    def double_click(self, x, y):
        """텍스트를 두 번 클릭하면 다시 편집한다."""
        if self.editing or self.tool == "crop":
            return
        shape = self.shape_at(*self.to_image(x, y))
        if shape is not None and shape.kind == "text":
            self.start_text(shape.x1, shape.y1, shape)

    # ── 텍스트 ────────────────────────────────────────────────────────────
    def start_text(self, ix, iy, shape=None):
        if self.editing:
            self.commit_text()
        self.editing = {
            "shape": shape,
            "at": (ix, iy),
            "color": shape.color if shape else self.color,
            "size": shape.size if shape else self.text_size(),
            "text": shape.text if shape else "",
        }
        self.selected = shape
        self.editor.show_text_entry(self.editing)
        self.queue_draw()

    def commit_text(self, cancel=False):
        editing, self.editing = self.editing, None
        if not editing:
            return
        text = self.editor.hide_text_entry().rstrip()
        shape = editing["shape"]
        if cancel:
            pass
        elif shape is not None:
            changed = (text, editing["color"], editing["size"]) != (shape.text, shape.color, shape.size)
            if changed:
                self.checkpoint()
                if text:
                    shape.text, shape.color, shape.size = text, editing["color"], editing["size"]
                    self.measure_text(shape)  # 글자 상자 크기를 바로 갱신 (다음 클릭 판정용)
                else:
                    self.shapes.remove(shape)
                    self.selected = None
        elif text.strip():
            self.checkpoint()
            shape = Shape("text", editing["color"])
            shape.x1, shape.y1 = editing["at"]
            shape.text = text
            shape.size = editing["size"]
            self.measure_text(shape)
            self.shapes.append(shape)
            self.selected = shape
        self.grab_focus()
        self.queue_draw()

    # ── 자르기 ────────────────────────────────────────────────────────────
    def apply_crop(self):
        if not self.crop_draft:
            return
        x, y, w, h = self.crop_draft
        x0, y0 = max(0, round(x)), max(0, round(y))
        x1, y1 = min(self.tex_w, round(x + w)), min(self.tex_h, round(y + h))
        new = (x0, y0, max(1, x1 - x0), max(1, y1 - y0))
        if new != self.crop:
            self.checkpoint()
            self.crop = new
