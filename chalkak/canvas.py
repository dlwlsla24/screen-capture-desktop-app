"""편집 캔버스(GTK): 이미지 + 도형을 그리고 마우스 입력을 core.EditState 로 넘긴다."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gsk", "4.0")
from gi.repository import Gdk, Gsk, Gtk  # noqa: E402

from .core import CROP_HANDLES, PAD, PAD_BOTTOM, PAD_TOP, EditState  # noqa: F401  (editor.py 가 PAD 를 가져다 쓴다)
from .shapes import draw_shape, text_layout
from .util import point, rect, rgba, texture_from_pixbuf

HANDLE_R = 5.5

ACCENT = rgba(0.21, 0.52, 0.89, 1)
WHITE = rgba(1, 1, 1, 1)
CROP_DIM = rgba(0, 0, 0, 0.6)
FRAME = rgba(1, 1, 1, 0.14)


class Canvas(Gtk.Widget, EditState):
    def __init__(self, editor, pixbuf, ratio=1.0):
        super().__init__()
        self.editor = editor
        self.texture = texture_from_pixbuf(pixbuf)
        self.pango = self.get_pango_context()
        self.init_edit_state(pixbuf.get_width(), pixbuf.get_height(), ratio)

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
        motion.connect("motion", lambda _ctrl, x, y: self.pointer_motion(x, y))
        self.add_controller(motion)

    def measure_text(self, shape):
        text_layout(shape, self.pango)

    # ── 마우스 → core ─────────────────────────────────────────────────────
    @staticmethod
    def _shift(gesture):
        return bool(gesture.get_current_event_state() & Gdk.ModifierType.SHIFT_MASK)

    def _drag_begin(self, gesture, x, y):
        self.pointer_press(x, y, self._shift(gesture))

    def _drag_update(self, gesture, dx, dy):
        ok, sx, sy = gesture.get_start_point()
        if ok:
            self.pointer_drag(sx + dx, sy + dy, self._shift(gesture))

    def _drag_end(self, _gesture, dx, dy):
        self.pointer_release(dx, dy)

    def _clicked(self, _gesture, n_press, x, y):
        if n_press == 2:
            self.double_click(x, y)

    # ── 그리기 ────────────────────────────────────────────────────────────
    def draw_scene(self, snapshot, k=1.0, final=False):
        if k < 0.999:
            filt = Gsk.ScalingFilter.TRILINEAR
        elif k > 1.001:
            filt = Gsk.ScalingFilter.NEAREST
        else:
            filt = Gsk.ScalingFilter.LINEAR
        snapshot.append_scaled_texture(self.texture, filt, rect(0, 0, self.tex_w, self.tex_h))
        for shape in self.scene_items(final):
            draw_shape(snapshot, shape, self)

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
