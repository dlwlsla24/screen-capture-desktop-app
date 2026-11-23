"""GTK3 helper; exit with EDIT only after the thumbnail was activated."""

import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk

# Executed by filename so the installed launcher needs no PYTHONPATH.
from preview import EDIT, TIMEOUT_MS


def main(path):
    ok, _ = Gtk.init_check()
    if not ok:
        return 1
    display = Gdk.Display.get_default()
    pointer = display.get_default_seat().get_pointer()
    _, x, y = pointer.get_position()
    area = display.get_monitor_at_point(x, y).get_workarea()
    margin = 18
    pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
        path, min(244, max(1, area.width - 2 * margin - 24)),
        min(148, max(1, area.height - 2 * margin - 64)), True
    )
    window = Gtk.Window(type=Gtk.WindowType.POPUP)
    window.set_title("심플찰칵 캡처 미리보기")
    window.set_name("chalkak-preview")
    window.set_accept_focus(False)
    window.set_focus_on_map(False)
    window.set_resizable(False)
    css = Gtk.CssProvider()
    css.load_from_data(b"""
        #chalkak-preview { background: #26262a; border: 1px solid #888; }
        #chalkak-preview button { background: transparent; color: white; border: 0;
            box-shadow: none; padding: 4px; }
        #chalkak-preview button:hover { background: #47474d; }
    """)
    Gtk.StyleContext.add_provider_for_screen(window.get_screen(), css, 600)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    box.set_border_width(5)
    window.add(box)
    edit = Gtk.Button()
    edit.set_image(Gtk.Image.new_from_pixbuf(pixbuf))
    edit.get_accessible().set_name("캡처 편집")
    box.pack_start(edit, True, True, 0)
    row = Gtk.Box(spacing=4)
    edit_label = Gtk.Button(label="캡처 편집")
    close = Gtk.Button.new_from_icon_name("window-close-symbolic", Gtk.IconSize.MENU)
    close.get_accessible().set_name("닫기")
    row.pack_start(edit_label, True, True, 0)
    row.pack_end(close, False, False, 0)
    box.pack_start(row, False, False, 0)

    status = 0

    def activate(*_):
        nonlocal status
        status = EDIT
        window.destroy()

    edit.connect("clicked", activate)
    edit_label.connect("clicked", activate)
    close.connect("clicked", lambda *_: window.destroy())
    window.connect("destroy", lambda *_: Gtk.main_quit())
    box.show_all()
    _, size = window.get_preferred_size()
    window.move(area.x + area.width - size.width - margin,
                area.y + area.height - size.height - margin)
    window.show()
    GLib.timeout_add(TIMEOUT_MS, window.destroy)
    Gtk.main()
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
