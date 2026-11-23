"""편집 창: 위쪽 도구 막대 + 캔버스 + 아래 색·굵기 막대."""

import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from .canvas import PAD, PAD_BOTTOM, PAD_TOP, Canvas
from .icons import Dot, Icon
from .util import copy_png, hex_rgb, new_save_path

# (도구, 이름, 글자 단축키)
TOOLS = [
    ("arrow", "화살표", "A"),
    ("rect", "네모", "R"),
    ("ellipse", "동그라미", "O"),
    ("triangle", "세모", "G"),
    ("pen", "펜", "P"),
    ("text", "텍스트", "T"),
    ("blur", "블러 (가리기)", "B"),
    ("crop", "자르기", "C"),
]
COLORS = ["#ff3b30", "#ff9500", "#ffd60a", "#34c759", "#0a84ff", "#1c1c1e", "#ffffff"]
COLOR_NAMES = ["빨강", "주황", "노랑", "초록", "파랑", "검정", "흰색"]
SIZE_NAMES = ["가늘게", "보통", "굵게"]
TEXT_PAD_X, TEXT_PAD_Y = 7, 4  # 입력 상자 안쪽 여백 + 테두리


class EditorWindow(Adw.ApplicationWindow):
    def __init__(self, app, pixbuf, ratio=1.0, source_path=None):
        super().__init__(application=app, title="찰칵 편집")
        self.source_path = source_path
        self._syncing = False
        self._previous_tool = "arrow"
        self._text_editing = None
        self._preedit = ""
        self._text_view = self._build_text_entry()

        self.canvas = Canvas(self, pixbuf, ratio)
        self.overlay = Gtk.Overlay()
        self.overlay.set_child(self.canvas)
        self.overlay.add_overlay(self._build_pill())
        self.overlay.add_overlay(self._text_view)
        self.overlay.add_overlay(self._build_toast())

        view = Adw.ToolbarView()
        view.add_top_bar(self._build_header())
        view.set_content(self.overlay)
        self.set_content(view)

        self._text_css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(), self._text_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1
        )

        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._key_pressed)
        self.add_controller(keys)

        self.connect("close-request", self._closing)
        self._set_initial_size(pixbuf, ratio)
        self.sync_actions()
        self.canvas.grab_focus()

    def _closing(self, _window):
        if self._toast_timer:
            GLib.source_remove(self._toast_timer)
            self._toast_timer = 0
        Gtk.StyleContext.remove_provider_for_display(self.get_display(), self._text_css)
        return False

    # ── 구성 ──────────────────────────────────────────────────────────────
    def _build_header(self):
        header = Adw.HeaderBar()

        self.undo_button = self._icon_button("edit-undo-symbolic", "실행 취소 (Ctrl+Z)", self.canvas.undo)
        self.redo_button = self._icon_button(
            "edit-redo-symbolic", "다시 실행 (Ctrl+Shift+Z)", self.canvas.redo
        )
        header.pack_start(self.undo_button)
        header.pack_start(self.redo_button)

        tools = Gtk.Box(spacing=3)
        tools.add_css_class("chalkak-tools")
        self.tool_buttons = {}
        first = None
        for index, (tool, name, key) in enumerate(TOOLS, start=1):
            button = Gtk.ToggleButton()
            button.set_child(Icon(tool))
            button.add_css_class("flat")
            button.add_css_class("chalkak-tool")
            button.set_focusable(False)
            button.set_tooltip_text(f"{name}  ({index} 또는 {key})")
            if first is None:
                first = button
                button.set_active(True)
            else:
                button.set_group(first)
            button.connect("toggled", self._tool_toggled, tool)
            if tool in ("text", "blur"):
                tools.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL, margin_start=3, margin_end=3))
            tools.append(button)
            self.tool_buttons[tool] = button
        header.set_title_widget(tools)

        done = Gtk.Button(label="복사하고 닫기")
        done.add_css_class("suggested-action")
        done.set_focusable(False)
        done.set_tooltip_text("결과를 클립보드에 복사하고 창을 닫습니다  (Enter)")
        done.connect("clicked", lambda *_: self.copy_and_close())
        header.pack_end(done)
        header.pack_end(self._icon_button("document-save-symbolic", "사진 폴더에 저장 (Ctrl+S)", self.save))
        return header

    @staticmethod
    def _icon_button(icon, tooltip, callback):
        button = Gtk.Button(icon_name=icon)
        button.set_focusable(False)
        button.set_tooltip_text(tooltip)
        button.connect("clicked", lambda *_: callback())
        return button

    def _build_pill(self):
        self.pill = Gtk.Stack()
        self.pill.set_halign(Gtk.Align.CENTER)
        self.pill.set_valign(Gtk.Align.END)
        self.pill.set_margin_bottom(20)
        self.pill.set_hhomogeneous(False)
        self.pill.add_css_class("chalkak-pill")

        style = Gtk.Box(spacing=2)
        self.color_buttons = []
        first = None
        for value, name in zip(COLORS, COLOR_NAMES):
            color = hex_rgb(value)
            button = self._dot_button(Dot(color, 8.0), name, first)
            first = first or button
            button.connect("toggled", self._color_toggled, color)
            style.append(button)
            self.color_buttons.append((color, button))
        style.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL, margin_start=8, margin_end=8))
        self.size_buttons = []
        first = None
        for index, radius in enumerate((2.5, 4.5, 7.0)):
            button = self._dot_button(Dot(None, radius), SIZE_NAMES[index], first)
            first = first or button
            button.connect("toggled", self._size_toggled, index)
            style.append(button)
            self.size_buttons.append(button)
        self.color_buttons[0][1].set_active(True)
        self.size_buttons[1].set_active(True)
        self.pill.add_named(style, "style")

        crop = Gtk.Box(spacing=10)
        label = Gtk.Label(label="남길 부분을 드래그로 정하세요")
        label.set_margin_start(6)
        crop.append(label)
        cancel = Gtk.Button(label="취소")
        cancel.set_focusable(False)
        cancel.set_tooltip_text("Esc")
        cancel.connect("clicked", lambda *_: self.finish_crop(apply=False))
        crop.append(cancel)
        apply = Gtk.Button(label="자르기")
        apply.add_css_class("suggested-action")
        apply.set_focusable(False)
        apply.set_tooltip_text("Enter")
        apply.connect("clicked", lambda *_: self.finish_crop(apply=True))
        crop.append(apply)
        self.pill.add_named(crop, "crop")
        return self.pill

    def _build_toast(self):
        self._toast_timer = 0
        self._toast_action = None
        self._toast_label = Gtk.Label()
        self._toast_button = Gtk.Button()
        self._toast_button.set_focusable(False)
        self._toast_button.add_css_class("flat")
        self._toast_button.connect("clicked", lambda *_: self._toast_action and self._toast_action())
        box = Gtk.Box(spacing=8)
        box.add_css_class("chalkak-pill")
        box.add_css_class("chalkak-toast")
        box.append(self._toast_label)
        box.append(self._toast_button)
        self._toast = Gtk.Revealer(child=box)
        self._toast.set_transition_type(Gtk.RevealerTransitionType.CROSSFADE)
        self._toast.set_transition_duration(160)
        self._toast.set_halign(Gtk.Align.CENTER)
        self._toast.set_valign(Gtk.Align.START)
        self._toast.set_margin_top(14)
        self._toast.set_can_target(False)
        return self._toast

    def toast(self, text, button=None, action=None, seconds=2.2):
        self._toast_label.set_label(text)
        self._toast_button.set_visible(button is not None)
        self._toast_button.set_label(button or "")
        self._toast_action = action
        self._toast.set_can_target(button is not None)
        self._toast.set_reveal_child(True)
        if self._toast_timer:
            GLib.source_remove(self._toast_timer)
        self._toast_timer = GLib.timeout_add(int(seconds * 1000), self._hide_toast)

    def _hide_toast(self):
        self._toast_timer = 0
        self._toast.set_reveal_child(False)
        self._toast.set_can_target(False)
        return GLib.SOURCE_REMOVE

    @staticmethod
    def _dot_button(child, tooltip, group):
        button = Gtk.ToggleButton()
        button.set_child(child)
        button.add_css_class("flat")
        button.add_css_class("chalkak-dot")
        button.set_focusable(False)
        button.set_tooltip_text(tooltip)
        if group is not None:
            button.set_group(group)
        return button

    def _set_initial_size(self, pixbuf, ratio):
        max_w, max_h = 1500, 950
        monitors = self.get_display().get_monitors()
        if monitors.get_n_items():
            geometry = monitors.get_item(0).get_geometry()
            max_w, max_h = int(geometry.width * 0.82), int(geometry.height * 0.82)
        width = pixbuf.get_width() / ratio + 2 * PAD
        height = pixbuf.get_height() / ratio + PAD_TOP + PAD_BOTTOM + 48
        self.set_default_size(int(min(max(width, 860), max_w)), int(min(max(height, 560), max_h)))

    # ── 도구·스타일 ───────────────────────────────────────────────────────
    def _tool_toggled(self, button, tool):
        if not button.get_active():
            return
        if tool != "crop":
            self._previous_tool = tool
        self.canvas.set_tool(tool)
        self.pill.set_visible_child_name("crop" if tool == "crop" else "style")

    def set_tool(self, tool):
        self.tool_buttons[tool].set_active(True)

    def finish_crop(self, apply):
        if apply:
            self.canvas.apply_crop()
        self.set_tool(self._previous_tool)

    def _color_toggled(self, button, color):
        if button.get_active() and not self._syncing:
            self.canvas.set_color(color)

    def _size_toggled(self, button, index):
        if button.get_active() and not self._syncing:
            self.canvas.set_size(index)

    def sync_style(self, shape):
        """도형을 선택하면 아래 막대가 그 도형의 색·굵기를 가리키게 한다."""
        self._syncing = True
        try:
            if shape.kind != "blur":
                for color, button in self.color_buttons:
                    if color == shape.color:
                        button.set_active(True)
                        self.canvas.color = color
            for index, button in enumerate(self.size_buttons):
                current = shape.size if shape.kind == "text" else shape.width
                target = self.canvas.text_size(index) if shape.kind == "text" else self.canvas.stroke_width(index)
                if abs(current - target) < 0.01:
                    button.set_active(True)
                    self.canvas.size_idx = index
        finally:
            self._syncing = False

    def sync_actions(self):
        self.undo_button.set_sensitive(bool(self.canvas.undo_stack))
        self.redo_button.set_sensitive(bool(self.canvas.redo_stack))

    # ── 텍스트 입력 상자 ──────────────────────────────────────────────────
    def _build_text_entry(self):
        """글자 입력 상자. 창마다 하나를 만들어 두고 숨겼다 보였다 한다.

        (입력 중에 위젯을 없애면 GTK 의 Wayland 입력기 처리에서 죽는 경우가 있어 재사용한다.)
        """
        view = Gtk.TextView()
        view.add_css_class("chalkak-text")
        view.set_accepts_tab(False)
        view.set_halign(Gtk.Align.START)
        view.set_valign(Gtk.Align.START)
        view.set_visible(False)
        view.get_buffer().connect("changed", lambda *_: self._fit_text_entry())
        view.connect("preedit-changed", self._preedit_changed)
        return view

    def show_text_entry(self, editing):
        view = self._text_view
        self._text_editing = editing
        self._preedit = ""
        view.get_buffer().set_text(editing["text"])
        sx, sy = self.canvas.to_screen(*editing["at"])
        view.set_margin_start(max(0, int(sx - TEXT_PAD_X)))
        view.set_margin_top(max(0, int(sy - TEXT_PAD_Y)))
        self.style_text_entry(editing)
        view.set_visible(True)
        view.grab_focus()
        buffer = view.get_buffer()
        buffer.place_cursor(buffer.get_end_iter())

    def _preedit_changed(self, _view, preedit):
        """한글처럼 조합 중인 글자. 아직 버퍼에 없으므로 따로 기억해 둔다."""
        self._preedit = preedit or ""
        self._fit_text_entry()

    def _fit_text_entry(self):
        """입력 상자를 글자 크기에 딱 맞춘다 (GtkTextView 는 혼자서는 높이를 못 잡는다)."""
        view = self._text_view
        if self._text_editing is None:
            return
        k, _ox, _oy = self.canvas.transform()
        px = max(8.0, self._text_editing["size"] * k)
        buffer = view.get_buffer()
        text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False) + self._preedit
        layout = view.create_pango_layout(text if text and not text.endswith("\n") else text + " ")
        font = Pango.FontDescription("Sans Bold")
        font.set_absolute_size(px * Pango.SCALE)
        layout.set_font_description(font)
        width, height = layout.get_pixel_size()
        view.set_size_request(
            int(max(width + px * 0.8, px * 2.5)) + 2 * TEXT_PAD_X, int(height) + 2 * TEXT_PAD_Y
        )

    def style_text_entry(self, editing):
        k, _ox, _oy = self.canvas.transform()
        r, g, b = (round(c * 255) for c in editing["color"])
        px = max(8.0, editing["size"] * k)
        self._text_css.load_from_string(
            f"""
            textview.chalkak-text {{
                font-family: Sans; font-weight: bold; font-size: {px:.1f}px;
                color: rgb({r},{g},{b}); caret-color: rgb({r},{g},{b});
                background: alpha(black, 0.28);
                border: 1px dashed alpha(white, 0.75); border-radius: 5px;
                padding: {TEXT_PAD_Y - 1}px {TEXT_PAD_X - 1}px;
            }}
            textview.chalkak-text text {{ background: none; color: rgb({r},{g},{b}); }}
            """
        )
        self._fit_text_entry()

    def hide_text_entry(self):
        """입력 상자를 숨기고 입력된 글을 돌려준다."""
        if self._text_editing is None:
            return ""
        view = self._text_view
        buffer = view.get_buffer()
        text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
        if self._preedit:
            # Enter·바깥 클릭으로 끝낼 때 조합 중이던 마지막 글자가 사라지지 않게 직접 붙인다.
            at = buffer.get_iter_at_mark(buffer.get_insert()).get_offset()
            text = text[:at] + self._preedit + text[at:]
        self._text_editing = None
        self._preedit = ""
        view.reset_im_context()
        self.canvas.grab_focus()
        view.set_visible(False)
        buffer.set_text("")
        return text

    # ── 키보드 ────────────────────────────────────────────────────────────
    def _key_pressed(self, _ctrl, keyval, _keycode, state):
        canvas = self.canvas
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        enter = keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter)

        if canvas.editing:
            if keyval == Gdk.KEY_Escape:
                canvas.commit_text(cancel=True)
                return True
            if enter and not shift:  # Shift+Enter 는 줄바꿈
                canvas.commit_text()
                return True
            return False

        key = Gdk.keyval_to_lower(keyval)
        if ctrl:
            if key == Gdk.KEY_z:
                canvas.redo() if shift else canvas.undo()
            elif key == Gdk.KEY_y:
                canvas.redo()
            elif key == Gdk.KEY_c:
                self.copy()
            elif key == Gdk.KEY_s:
                self.save()
            else:
                return False
            return True

        if enter:
            if canvas.tool == "crop":
                self.finish_crop(apply=True)
            else:
                self.copy_and_close()
            return True
        if keyval == Gdk.KEY_Escape:
            if canvas.tool == "crop":
                self.finish_crop(apply=False)
            elif not canvas.deselect() and not canvas.dirty:
                self.close()
            return True
        if keyval in (Gdk.KEY_Delete, Gdk.KEY_BackSpace):
            canvas.delete_selected()
            return True
        if state & (Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.SUPER_MASK):
            return False
        for index, (tool, _name, letter) in enumerate(TOOLS, start=1):
            if key in (Gdk.KEY_0 + index, Gdk.KEY_KP_0 + index, Gdk.keyval_from_name(letter.lower())):
                self.set_tool(tool)
                return True
        return False

    # ── 내보내기 ──────────────────────────────────────────────────────────
    def copy(self, toast=True):
        png = self.canvas.render_png()
        copy_png(self.get_display(), png)
        self.canvas.dirty = False
        if toast:
            self.toast("클립보드에 복사했어요")
        return png

    def copy_and_close(self):
        self.copy(toast=False)
        GLib.idle_add(self.close)

    def save(self):
        png = self.canvas.render_png()
        path = new_save_path()
        try:
            with open(path, "wb") as handle:
                handle.write(png)
        except OSError as err:
            self.toast(f"저장하지 못했어요: {err.strerror}", seconds=4)
            return None
        self.canvas.dirty = False
        self.toast(f"저장했어요 · {os.path.basename(path)}", "폴더 열기", lambda: self._reveal(path), seconds=5)
        return path

    def _reveal(self, path):
        launcher = Gtk.FileLauncher.new(Gio.File.new_for_path(path))
        launcher.open_containing_folder(self, None, None)
