"""영역 선택 오버레이.

단축키 → (화면 한 장 요청) → 모니터마다 전체 화면 창을 띄우고 드래그로 영역 선택
→ 마우스를 놓는 순간 잘라서 클립보드에 복사하고 닫는다.

창은 캡처 이미지가 도착한 뒤에 그 정지 화면을 깔고 뜬다. GNOME 은 투명한 전체 화면
창 뒤를 검게 칠하기 때문에, 이미지보다 먼저 띄우면 화면이 까맣게 번쩍인다.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, GLib, Gsk, Gtk, Pango  # noqa: E402

from .capture import ScreenGrab
from .util import copy_png, log, pixbuf_to_png, point, rect, rgba, texture_from_pixbuf

DIM = rgba(0, 0, 0, 0.42)
WHITE = rgba(1, 1, 1, 1)
EDGE_SHADOW = rgba(0, 0, 0, 0.55)
PILL_BG = rgba(0.08, 0.08, 0.09, 0.86)
MIN_SIZE = 4  # 이보다 작은 드래그는 실수로 본다


class SelectionArea(Gtk.Widget):
    """모니터 한 대를 덮는 그리기 영역."""

    def __init__(self, overlay, geometry):
        super().__init__()
        self.overlay = overlay
        self.geom = geometry  # (x, y, w, h) 논리 좌표
        self.texture = None
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

        cancel = Gtk.GestureClick()
        cancel.set_button(Gdk.BUTTON_SECONDARY)
        cancel.connect("pressed", lambda *_: self.overlay.cancel())
        self.add_controller(cancel)

    # ── 입력 ──────────────────────────────────────────────────────────────
    def _drag_begin(self, _gesture, x, y):
        self.overlay.begin(self, x, y)

    def _drag_update(self, gesture, dx, dy):
        ok, sx, sy = gesture.get_start_point()
        if ok:
            self.overlay.update(self, sx + dx, sy + dy)

    def _drag_end(self, gesture, dx, dy):
        ok, sx, sy = gesture.get_start_point()
        if ok:
            self.overlay.update(self, sx + dx, sy + dy)
        self.overlay.release(self)

    # ── 그리기 ────────────────────────────────────────────────────────────
    def do_snapshot(self, snapshot):
        ov = self.overlay
        w, h = self.get_width(), self.get_height()
        if w <= 0 or h <= 0:
            return
        full = rect(0, 0, w, h)
        if self.texture is not None:
            snapshot.append_texture(self.texture, full)

        sel = ov.selection_in(self)
        if sel is None:
            snapshot.append_color(DIM, full)
            if not ov.dragging:
                self._draw_pill(snapshot, "드래그해서 캡처   ·   Esc 취소", w / 2, 54, center=True)
            return

        x, y, sw, sh = sel
        snapshot.append_color(DIM, rect(0, 0, w, y))
        snapshot.append_color(DIM, rect(0, y + sh, w, h - y - sh))
        snapshot.append_color(DIM, rect(0, y, x, sh))
        snapshot.append_color(DIM, rect(x + sw, y, w - x - sw, sh))
        self._draw_frame(snapshot, x - 2, y - 2, sw + 4, sh + 4, 1, EDGE_SHADOW)
        self._draw_frame(snapshot, x - 1, y - 1, sw + 2, sh + 2, 1, WHITE)

        iw, ih = ov.image_size(self, sw, sh)
        label = f"{iw} × {ih}"
        ly = y + sh + 10
        if ly + 30 > h:
            ly = max(4, y - 38)
        self._draw_pill(snapshot, label, x + sw, ly, right=True)

    @staticmethod
    def _draw_frame(snapshot, x, y, w, h, t, color):
        snapshot.append_color(color, rect(x, y, w, t))
        snapshot.append_color(color, rect(x, y + h - t, w, t))
        snapshot.append_color(color, rect(x, y + t, t, h - 2 * t))
        snapshot.append_color(color, rect(x + w - t, y + t, t, h - 2 * t))

    def _draw_pill(self, snapshot, text, x, y, center=False, right=False):
        layout = self.create_pango_layout(text)
        font = Pango.FontDescription("Sans 600")
        font.set_absolute_size(14 * Pango.SCALE)
        layout.set_font_description(font)
        tw, th = layout.get_pixel_size()
        pw, ph = tw + 28, th + 14
        if center:
            x -= pw / 2
        elif right:
            x -= pw
        x = max(6, min(x, self.get_width() - pw - 6))
        box = Gsk.RoundedRect()
        box.init_from_rect(rect(x, y, pw, ph), ph / 2)
        snapshot.push_rounded_clip(box)
        snapshot.append_color(PILL_BG, rect(x, y, pw, ph))
        snapshot.pop()
        snapshot.save()
        snapshot.translate(point(x + 14, y + 7))
        snapshot.append_layout(layout, WHITE)
        snapshot.restore()


class SelectionOverlay:
    """한 번의 캡처 세션. 끝나면 on_done / on_cancel / on_error 중 하나를 부른다."""

    def __init__(self, app, on_done, on_cancel, on_error):
        self.app = app
        self._on_done = on_done
        self._on_cancel = on_cancel
        self._on_error = on_error
        self.display = Gdk.Display.get_default()
        self.pixbuf = None
        self.ratio = 1.0  # 이미지 픽셀 / 논리 픽셀
        self.windows = []
        self.areas = []
        self.dragging = False
        self.closed = False
        self.done = False  # 복사까지 끝나 닫히는 중
        self._active = None
        self._start = None
        self._end = None

        monitors = self.display.get_monitors()
        self.monitors = [monitors.get_item(i) for i in range(monitors.get_n_items())]
        geoms = [m.get_geometry() for m in self.monitors]
        self.bx = min(g.x for g in geoms)
        self.by = min(g.y for g in geoms)
        self.bw = max(g.x + g.width for g in geoms) - self.bx
        self.bh = max(g.y + g.height for g in geoms) - self.by
        self.geoms = [(g.x, g.y, g.width, g.height) for g in geoms]

        self.grab = ScreenGrab(self._image_ready, self._failed)
        self.grab.start()

    # ── 창 ────────────────────────────────────────────────────────────────
    def _present(self):
        pixbuf = self.pixbuf
        pw, ph = pixbuf.get_width(), pixbuf.get_height()
        for monitor, geom in zip(self.monitors, self.geoms):
            area = SelectionArea(self, geom)
            gx, gy, gw, gh = geom
            x = max(0, min(pw - 1, round((gx - self.bx) * self.ratio)))
            y = max(0, min(ph - 1, round((gy - self.by) * self.ratio)))
            w = max(1, min(pw - x, round(gw * self.ratio)))
            h = max(1, min(ph - y, round(gh * self.ratio)))
            area.texture = texture_from_pixbuf(pixbuf.new_subpixbuf(x, y, w, h))
            window = Gtk.Window(application=self.app)
            window.set_title("찰칵 — 영역 선택")
            window.set_decorated(False)
            window.add_css_class("chalkak-overlay")
            window.set_child(area)
            keys = Gtk.EventControllerKey()
            keys.connect("key-pressed", self._key_pressed)
            window.add_controller(keys)
            window.connect("close-request", self._close_request)
            window.fullscreen_on_monitor(monitor)
            window.present()
            self.windows.append(window)
            self.areas.append(area)

    def _close_request(self, _window):
        self.cancel()
        return True

    def _key_pressed(self, _ctrl, keyval, _keycode, _state):
        if keyval == Gdk.KEY_Escape:
            self.cancel()
            return True
        return False

    def _image_ready(self, pixbuf):
        if self.closed:
            return
        self.pixbuf = pixbuf
        self.ratio = pixbuf.get_width() / self.bw
        log(f"overlay: 화면 {pixbuf.get_width()}x{pixbuf.get_height()} 도착, 선택 창 표시")
        self._present()

    def _failed(self, message):
        self._close()
        self._on_error(message)

    # ── 선택 ──────────────────────────────────────────────────────────────
    def begin(self, area, x, y):
        if self.done:
            return
        self._active = area
        self._start = (x, y)
        self._end = (x, y)
        self.dragging = True
        self._redraw()

    def update(self, area, x, y):
        if self.done or area is not self._active:
            return
        x = max(0, min(area.get_width(), x))
        y = max(0, min(area.get_height(), y))
        self._end = (x, y)
        area.queue_draw()

    def release(self, area):
        if self.done or area is not self._active:
            return
        self.dragging = False
        sel = self.selection_in(area)
        if sel is None or sel[2] < MIN_SIZE or sel[3] < MIN_SIZE:
            self._active = None
            self._redraw()
            return
        self._finish()

    def selection_in(self, area):
        if area is not self._active or self._start is None:
            return None
        (x0, y0), (x1, y1) = self._start, self._end
        return (min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))

    def _scale_for(self, area):
        """위젯 좌표 → 이미지 픽셀 배율."""
        width = area.get_width() or area.geom[2]
        return area.geom[2] / width * self.ratio

    def image_size(self, area, w, h):
        k = self._scale_for(area)
        return max(1, round(w * k)), max(1, round(h * k))

    def _redraw(self):
        for area in self.areas:
            area.queue_draw()

    # ── 마무리 ────────────────────────────────────────────────────────────
    def _finish(self):
        area = self._active
        x, y, w, h = self.selection_in(area)
        k = self._scale_for(area)
        gx, gy, _gw, _gh = area.geom
        pw, ph = self.pixbuf.get_width(), self.pixbuf.get_height()
        ix = max(0, min(pw - 1, round((gx - self.bx) * self.ratio + x * k)))
        iy = max(0, min(ph - 1, round((gy - self.by) * self.ratio + y * k)))
        iw = max(1, min(pw - ix, round(w * k)))
        ih = max(1, min(ph - iy, round(h * k)))
        self.done = True
        crop = self.pixbuf.new_subpixbuf(ix, iy, iw, ih).copy()
        png = pixbuf_to_png(crop)
        # 우리 창이 아직 포커스를 가진 지금 클립보드에 올려야 Wayland 가 받아 준다.
        copy_png(self.display, png)
        ratio = self.ratio
        GLib.idle_add(self._finished, crop, png, ratio)

    def _finished(self, crop, png, ratio):
        self._close()
        self._on_done(crop, png, ratio)
        return GLib.SOURCE_REMOVE

    def cancel(self):
        if self.closed or self.done:
            return
        self._close()
        self._on_cancel()

    def _close(self):
        if self.closed:
            return
        self.closed = True
        self.grab.cancel()
        for window in self.windows:
            window.destroy()
        self.windows = []
        self.areas = []
        self.pixbuf = None
