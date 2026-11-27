"""Resident GTK3/X11 popup. The socket closes when the owning GTK4 service exits."""

import os
import socket
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkX11", "3.0")
from gi.repository import Gdk, GdkPixbuf, GdkX11, GLib, Gtk

from picker_geometry import crop_bounds, match_outputs
import picker_ipc


def geometry(monitor):
    r = monitor.get_geometry()
    return r.x, r.y, r.width, r.height


class Clipboard:
    """Serve the exact PNG to X11 and Mutter's Wayland bridge after the popups close."""

    def __init__(self):
        self.owner = Gtk.Invisible()
        self.owner.realize()
        self.selection = Gdk.SELECTION_CLIPBOARD
        self.target = Gdk.Atom.intern("image/png", False)
        Gtk.selection_add_target(self.owner, self.selection, self.target, 0)
        self.png = None
        self.owner.connect("selection-get", self._provide)
        self.owner.connect("selection-clear-event", self._cleared)

    def _provide(self, _widget, data, _info, _time):
        if self.png is not None:
            data.set(self.target, 8, self.png)

    def _cleared(self, *_args):
        self.png = None
        return False

    def claim(self, timestamp):
        if not Gtk.selection_owner_set(self.owner, self.selection, timestamp):
            raise ValueError("클립보드가 이미지를 받지 못했습니다.")


class Picker:
    def __init__(self, sock):
        self.sock = sock
        self.display = Gdk.Display.get_default()
        self.seat = self.display.get_default_seat()
        self.clipboard = Clipboard()
        self.css = Gtk.CssProvider()
        self.css.load_from_data(b"#shade { background-color: rgba(0, 0, 0, .22); }"
                                b"#border { background-color: white; }"
                                b"#keyboard, #keyboard decoration { background: transparent;"
                                b" border: none; box-shadow: none; }")
        self.redraw = []
        self.windows = []
        self.outputs = []
        self.image = self.origin = self.end = None
        self.keys = None
        self.grabbed = False
        self.serial = 0

    def command(self, _fd, _condition):
        try:
            message, fd = picker_ipc.receive(self.sock)
        except (OSError, EOFError):
            self.close()
            Gtk.main_quit()
            return GLib.SOURCE_REMOVE
        try:
            if message["command"] == "show":
                self.close()
                self.serial = message["id"]
                data = GLib.MappedFile.new_from_fd(fd, False).get_bytes()
                self.image = GdkPixbuf.Pixbuf.new_from_bytes(
                    data, GdkPixbuf.Colorspace.RGB, message["alpha"], 8,
                    message["width"], message["height"], message["stride"])
                self.show(message["monitors"], message["layout"])
            elif message["command"] == "nudge":
                self.nudge(message["monitors"], message["layout"])
            elif message["id"] == self.serial:
                self.close()
        except Exception as exc:
            self.finish(error=str(exc))
        finally:
            if fd is not None:
                os.close(fd)
        return GLib.SOURCE_CONTINUE

    def available(self):
        screen = self.display.get_default_screen()
        return [(screen.get_monitor_plug_name(i), geometry(self.display.get_monitor(i)))
                for i in range(self.display.get_n_monitors())]

    def nudge(self, monitors, layout):
        """멈춰 있는 모니터를 다시 그리게 한다: 완전히 투명한 1픽셀 창을 띄웠다가 곧 없앤다."""
        visual = self.display.get_default_screen().get_rgba_visual()
        if visual is None:
            return  # 투명하게 못 만들면 화면에 점이 찍히므로 하지 않는다
        for x, y, _w, _h in match_outputs(monitors, layout, self.available()):
            pixel = Gtk.Window(type=Gtk.WindowType.POPUP)
            pixel.set_visual(visual)
            pixel.set_app_paintable(True)
            pixel.set_accept_focus(False)
            pixel.set_default_size(1, 1)
            pixel.move(x, y)
            pixel.show()
            GLib.timeout_add(50, lambda window=pixel: window.destroy() and False)
        self.display.flush()

    def show(self, monitors, layout):
        screen = self.display.get_default_screen()
        available = self.available()
        self.regions = [tuple(region) for _, region in monitors]
        outputs = match_outputs(monitors, layout, available)
        self.outputs = list(zip(outputs, self.regions))
        for output, region in self.outputs:
            self.prepare_window(output, region)

        # An override-redirect popup cannot itself request compositor keyboard
        # focus. Activate a transparent utility on this same X11 connection;
        # its EWMH activation timestamp is scoped to this explicit selection.
        self.keys = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        self.keys.set_title("심플찰칵 입력")
        self.keys.set_type_hint(Gdk.WindowTypeHint.NORMAL)
        self.keys.set_decorated(False)
        self.keys.set_skip_taskbar_hint(True)
        self.keys.set_skip_pager_hint(True)
        self.keys.set_resizable(False)
        self.keys.set_default_size(1, 1)
        # Mutter can discard _NET_WM_WINDOW_OPACITY during unmap. Keep the
        # backing pixels themselves transparent, including the last frame.
        visual = screen.get_rgba_visual()
        if visual is None:
            raise ValueError("투명 영역 선택기에 필요한 XWayland 비주얼이 없습니다.")
        self.keys.set_visual(visual)
        self.keys.set_name("keyboard")
        self.keys.get_style_context().add_provider(self.css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.keys.set_app_paintable(True)
        pixel = Gtk.Fixed()
        pixel.set_size_request(1, 1)
        self.keys.add(pixel)
        pixel.show()
        self.keys.set_opacity(0)
        self.keys.move(*self.outputs[0][0][:2])
        self.keys.connect("key-press-event", self.key)
        self.keys.realize()
        def focus_mapped(widget, _event):
            surface = widget.get_window()
            surface.focus(GdkX11.x11_get_server_time(surface))
            return False

        self.keys.connect("map-event", focus_mapped)
        timestamp = GdkX11.x11_get_server_time(self.keys.get_window())
        self.keys.present_with_time(timestamp)

        for window in self.windows:
            window.show()
        cursor = Gdk.Cursor.new_from_name(self.display, "crosshair")
        caps = Gdk.SeatCapabilities.POINTER | Gdk.SeatCapabilities.KEYBOARD
        status = self.seat.grab(self.windows[0].get_window(), caps, False, cursor, None, None, None)
        if status != Gdk.GrabStatus.SUCCESS:
            raise ValueError("영역 선택기의 마우스와 키보드를 사용할 수 없습니다.")
        self.grabbed = True
        self.display.flush()

    def prepare_window(self, output, region):
        x, y, w, h = output
        lx, ly, lw, lh = region
        bounds = crop_bounds(self.regions, self.image.get_width(), self.image.get_height(),
                             (lx, ly), (lx + lw, ly + lh))
        tile = self.image.new_subpixbuf(*bounds)
        window = Gtk.Window(type=Gtk.WindowType.POPUP)
        self.windows.append(window)
        window.set_title("심플찰칵 영역 선택")
        window.set_decorated(False)
        window.set_app_paintable(True)
        window.set_resizable(False)
        window.set_size_request(w, h)
        window.move(x, y)
        window.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK |
                          Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.KEY_PRESS_MASK)
        window.connect("button-press-event", self.press)
        window.connect("motion-notify-event", self.motion)
        window.connect("button-release-event", self.release)
        window.connect("key-press-event", self.key)
        layout = Gtk.Fixed()
        window.add(layout)
        if tile.get_width() != w or tile.get_height() != h:
            tile = tile.scale_simple(w, h, GdkPixbuf.InterpType.BILINEAR)
        layout.put(Gtk.Image.new_from_pixbuf(tile), 0, 0)

        def block(name):
            widget = Gtk.EventBox()
            widget.set_above_child(False)
            widget.set_name(name)
            widget.get_style_context().add_provider(self.css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            layout.put(widget, 0, 0)
            return widget

        shades = [block("shade") for _ in range(4)]
        borders = [block("border") for _ in range(4)]
        layout.show_all()

        def rect(widget, x0, y0, x1, y1):
            x0, y0 = max(0, x0), max(0, y0)
            x1, y1 = min(w, x1), min(h, y1)
            if x1 <= x0 or y1 <= y0:
                widget.hide()
            else:
                layout.move(widget, x0, y0)
                widget.set_size_request(x1 - x0, y1 - y0)
                widget.show()

        def redraw():
            if self.origin is None:
                left = right = top = bottom = 0
            else:
                ax, bx = sorted((self.origin[0], self.end[0]))
                ay, by = sorted((self.origin[1], self.end[1]))
                left, right = (round((a - lx) * w / lw) for a in (ax, bx))
                top, bottom = (round((a - ly) * h / lh) for a in (ay, by))
            for widget, bounds in zip(shades, [(0, 0, w, top), (0, bottom, w, h),
                                               (0, top, left, bottom), (right, top, w, bottom)]):
                rect(widget, *bounds)
            for widget, bounds in zip(borders, [(left, top, right, top + 1),
                                                (left, bottom - 1, right, bottom),
                                                (left, top, left + 1, bottom),
                                                (right - 1, top, right, bottom)]):
                rect(widget, *bounds)

        self.redraw.append(redraw)
        redraw()
        window.realize()

    def point(self, event):
        for (x, y, w, h), (lx, ly, lw, lh) in self.outputs:
            if x <= event.x_root < x + w and y <= event.y_root < y + h:
                return (lx + (event.x_root - x) * lw / w,
                        ly + (event.y_root - y) * lh / h)
        return None

    def press(self, _window, event):
        if event.button == 1:
            self.origin = self.end = self.point(event)
        return True

    def motion(self, _window, event):
        if self.origin is not None:
            self.end = self.point(event) or self.end
            for redraw in self.redraw:
                redraw()
        return True

    def key(self, _window, event):
        if event.keyval == Gdk.KEY_Escape:
            self.finish()
        return True

    def release(self, _window, event):
        if event.button != 1:
            return True
        end = self.point(event)
        bounds = None
        if self.origin and end:
            bounds = crop_bounds(self.regions, self.image.get_width(), self.image.get_height(),
                                 self.origin, end)
        if not bounds:
            self.finish()
            return True
        try:
            crop = self.image.new_subpixbuf(*bounds)
            self.clipboard.claim(event.time)
            self.close()  # Release all input before PNG compression and disk I/O.
            _, png = crop.save_to_bufferv("png", ["compression"], ["1"])
            self.clipboard.png = png
            self.finish(png=png)
        except Exception as exc:
            self.finish(error=str(exc))
        return True

    def finish(self, png=None, error=None):
        self.close()
        result = {"id": self.serial}
        if error:
            result["error"] = error
        picker_ipc.send(self.sock, result, png)

    def close(self):
        if self.grabbed:
            self.seat.ungrab()
            self.grabbed = False
        for window in self.windows:
            window.destroy()
        self.windows.clear()
        if self.keys is not None:
            self.keys.destroy()
            self.keys = None
        self.outputs.clear()
        self.redraw.clear()
        self.image = self.origin = self.end = None
        self.display.flush()


def main():
    ok, _ = Gtk.init_check()
    if not ok:
        return 1
    with socket.socket(fileno=int(sys.argv[1])) as sock:
        picker = Picker(sock)
        GLib.io_add_watch(sock.fileno(), GLib.PRIORITY_DEFAULT, GLib.IO_IN | GLib.IO_HUP,
                          picker.command)
        Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
