"""편집 캔버스: 이미지 + 도형을 그리고, 마우스로 그리기·옮기기·크기조절·자르기를 처리한다."""

import math

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gsk", "4.0")
from gi.repository import Gdk, Gsk, Gtk  # noqa: E402

from .shapes import BOX_KINDS, Shape, constrain
from .util import point, rect, rgba, texture_from_pixbuf

PAD = 28
PAD_TOP = 24
PAD_BOTTOM = 84  # 아래 떠 있는 색·굵기 막대 자리
HANDLE_R = 5.5
HANDLE_HIT = 11
MIN_CROP = 12

STROKE_SIZES = (2.5, 4.5, 8.0)
TEXT_SIZES = (18.0, 28.0, 44.0)

ACCENT = rgba(0.21, 0.52, 0.89, 1)
WHITE = rgba(1, 1, 1, 1)
CROP_DIM = rgba(0, 0, 0, 0.6)
FRAME = rgba(1, 1, 1, 0.14)

CROP_HANDLES = {
    "nw": (0, 0), "n": (0.5, 0), "ne": (1, 0),
    "w": (0, 0.5), "e": (1, 0.5),
    "sw": (0, 1), "s": (0.5, 1), "se": (1, 1),
}  # fmt: skip
CROP_CURSORS = {
    "nw": "nwse-resize", "se": "nwse-resize", "ne": "nesw-resize", "sw": "nesw-resize",
    "n": "ns-resize", "s": "ns-resize", "w": "ew-resize", "e": "ew-resize",
}  # fmt: skip


class Canvas(Gtk.Widget):
    def __init__(self, editor, pixbuf, ratio=1.0):
        super().__init__()
        self.editor = editor
        self.ratio = max(ratio, 0.1)  # 이미지 픽셀 / 화면 논리 픽셀
        self.texture = texture_from_pixbuf(pixbuf)
        self.tex_w = pixbuf.get_width()
        self.tex_h = pixbuf.get_height()
        self.pango = self.get_pango_context()

        self.shapes = []
        self.crop = (0, 0, self.tex_w, self.tex_h)
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

        self.add_css_class("chalkak-canvas")
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_focusable(True)
        self.set_cursor_from_name("crosshair")

        drag = Gtk.GestureDrag()
        drag.set_button(Gdk.BUTTON_PRIMARY)
        drag.connect("drag-begin", self._drag_begin)
        drag.connect("drag-update", self._drag_update)
        drag.connect("drag-end", self._drag_end)
        self.add_controller(drag)

        click = Gtk.GestureClick()
        click.set_button(Gdk.BUTTON_PRIMARY)
        click.connect("released", self._clicked)
        self.add_controller(click)

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._motion)
        self.add_controller(motion)

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
                shape.layout(self)
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

    # ── 그리기 ────────────────────────────────────────────────────────────
    def draw_scene(self, snapshot, k=1.0, final=False):
        if k < 0.999:
            filt = Gsk.ScalingFilter.TRILINEAR
        elif k > 1.001:
            filt = Gsk.ScalingFilter.NEAREST
        else:
            filt = Gsk.ScalingFilter.LINEAR
        snapshot.append_scaled_texture(self.texture, filt, rect(0, 0, self.tex_w, self.tex_h))
        items = list(self.shapes)
        if self.draft is not None and not final:
            items.append(self.draft)
        hidden = self.editing["shape"] if self.editing and not final else None
        for shape in items:
            if shape.kind == "blur":
                shape.draw(snapshot, self)
        for shape in items:
            if shape.kind != "blur" and shape is not hidden:
                shape.draw(snapshot, self)

    def do_snapshot(self, snapshot):
        k, ox, oy = self.transform()
        vx, vy, vw, vh = self.view_rect()
        sx, sy = vx * k + ox, vy * k + oy
        snapshot.append_color(FRAME, rect(sx - 1, sy - 1, vw * k + 2, vh * k + 2))
        snapshot.save()
        snapshot.translate(point(ox, oy))
        snapshot.scale(k, k)
        snapshot.push_clip(rect(vx, vy, vw, vh))
        self.draw_scene(snapshot, k)
        snapshot.pop()
        snapshot.restore()
        if self.tool == "crop" and self.crop_draft:
            self._draw_crop(snapshot, k, ox, oy)
        elif self.selected is not None and self.selected is not (self.editing or {}).get("shape"):
            self._draw_selection(snapshot, k, ox, oy)

    def _draw_selection(self, snapshot, k, ox, oy):
        shape = self.selected
        if shape.kind != "arrow" and shape.kind != "rect":
            bx, by, bw, bh = shape.bounds()
            pad = 5 if shape.kind in ("pen", "text") else 0
            x, y = bx * k + ox - pad, by * k + oy - pad
            w, h = bw * k + 2 * pad, bh * k + 2 * pad
            builder = Gsk.PathBuilder.new()
            builder.add_rect(rect(x, y, w, h))
            stroke = Gsk.Stroke.new(1.0)
            stroke.set_dash([4.0, 3.0])
            snapshot.append_stroke(builder.to_path(), stroke, ACCENT)
        for _name, hx, hy in shape.handles():
            self._draw_handle(snapshot, hx * k + ox, hy * k + oy)

    @staticmethod
    def _draw_handle(snapshot, x, y, square=False):
        builder = Gsk.PathBuilder.new()
        if square:
            builder.add_rect(rect(x - 5, y - 5, 10, 10))
        else:
            builder.add_circle(point(x, y), HANDLE_R)
        path = builder.to_path()
        snapshot.append_fill(path, Gsk.FillRule.WINDING, WHITE)
        snapshot.append_stroke(path, Gsk.Stroke.new(1.6), ACCENT)

    def _draw_crop(self, snapshot, k, ox, oy):
        cx, cy, cw, ch = self.crop_draft
        x, y, w, h = cx * k + ox, cy * k + oy, cw * k, ch * k
        ix, iy = ox, oy
        iw, ih = self.tex_w * k, self.tex_h * k
        snapshot.append_color(CROP_DIM, rect(ix, iy, iw, y - iy))
        snapshot.append_color(CROP_DIM, rect(ix, y + h, iw, iy + ih - y - h))
        snapshot.append_color(CROP_DIM, rect(ix, y, x - ix, h))
        snapshot.append_color(CROP_DIM, rect(x + w, y, ix + iw - x - w, h))
        builder = Gsk.PathBuilder.new()
        builder.add_rect(rect(x, y, w, h))
        snapshot.append_stroke(builder.to_path(), Gsk.Stroke.new(1.5), WHITE)
        for fx, fy in CROP_HANDLES.values():
            self._draw_handle(snapshot, x + w * fx, y + h * fy, square=True)

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

    # ── 마우스 ────────────────────────────────────────────────────────────
    def _motion(self, _ctrl, x, y):
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

    def _drag_begin(self, gesture, x, y):
        self.grab_focus()
        self.drag = None
        if self.editing:
            self.commit_text()
            return
        ix, iy = self.to_image(x, y)
        shift = bool(gesture.get_current_event_state() & Gdk.ModifierType.SHIFT_MASK)

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

    def _drag_update(self, gesture, dx, dy):
        drag = self.drag
        if not drag:
            return
        ok, sx, sy = gesture.get_start_point()
        if not ok:
            return
        ix, iy = self.to_image(sx + dx, sy + dy)
        shift = bool(gesture.get_current_event_state() & Gdk.ModifierType.SHIFT_MASK)
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

    def _drag_end(self, gesture, dx, dy):
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

    def _clicked(self, gesture, n_press, x, y):
        """텍스트를 두 번 클릭하면 다시 편집한다."""
        if n_press != 2 or self.editing or self.tool == "crop":
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
                    shape.layout(self)  # 글자 상자 크기를 바로 갱신 (다음 클릭 판정용)
                else:
                    self.shapes.remove(shape)
                    self.selected = None
        elif text.strip():
            self.checkpoint()
            shape = Shape("text", editing["color"])
            shape.x1, shape.y1 = editing["at"]
            shape.text = text
            shape.size = editing["size"]
            shape.layout(self)
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

    # ── 내보내기 ──────────────────────────────────────────────────────────
    def render_png(self):
        """현재 결과물을 원본 해상도 PNG 바이트로 만든다."""
        if self.editing:
            self.commit_text()
        cx, cy, cw, ch = self.crop
        snapshot = Gtk.Snapshot()
        snapshot.translate(point(-cx, -cy))
        snapshot.push_clip(rect(cx, cy, cw, ch))
        self.draw_scene(snapshot, 1.0, final=True)
        snapshot.pop()
        node = snapshot.to_node()
        native = self.get_native()
        renderer = native.get_renderer() if native is not None else None
        own = None
        if renderer is None:
            own = renderer = Gsk.CairoRenderer()
            renderer.realize_for_display(self.get_display())
        try:
            texture = renderer.render_texture(node, rect(0, 0, cw, ch))
            return bytes(texture.save_to_png_bytes().get_data())
        finally:
            if own is not None:
                own.unrealize()
