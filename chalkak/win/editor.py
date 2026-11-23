"""편집 창 (Windows 판, tkinter). 편집 로직은 리눅스 판과 같은 core.EditState 를 쓴다."""

import os
import tkinter as tk
from tkinter import font as tkfont

from PIL import ImageTk

from ..core import CROP_HANDLES, PAD, PAD_BOTTOM, PAD_TOP, EditState
from . import system
from .render import UI_FONT, measure_text, render_scene, rgb, scale_base, tool_icon

# (도구, 이름, 글자 단축키)
TOOLS = [
    ("arrow", "화살표", "a"),
    ("rect", "네모", "r"),
    ("ellipse", "동그라미", "o"),
    ("triangle", "세모", "g"),
    ("pen", "펜", "p"),
    ("text", "텍스트", "t"),
    ("blur", "블러 (가리기)", "b"),
    ("crop", "자르기", "c"),
]
COLORS = ["#ff3b30", "#ff9500", "#ffd60a", "#34c759", "#0a84ff", "#1c1c1e", "#ffffff"]
COLOR_NAMES = ["빨강", "주황", "노랑", "초록", "파랑", "검정", "흰색"]
SIZE_NAMES = ["가늘게", "보통", "굵게"]

BAR_BG = "#2a2a2e"
BAR_HOVER = "#3a3a40"
CANVAS_BG = "#1b1b1e"
ACCENT = "#3584e4"
PRIMARY = "#e5484d"
TEXT_PAD = 5

CURSORS = {
    "crosshair": "crosshair", "text": "xterm", "move": "fleur", "pointer": "hand2",
    "nwse-resize": "size_nw_se", "nesw-resize": "size_ne_sw", "ns-resize": "size_ns", "ew-resize": "size_we",
}  # fmt: skip


def hex_rgb(value):
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) / 255 for i in (0, 2, 4))


class Tooltip:
    def __init__(self, widget, text):
        self.widget, self.text, self.tip = widget, text, None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _show(self, _event):
        if self.tip is not None:
            return
        self.tip = tk.Toplevel(self.widget)
        self.tip.overrideredirect(True)
        self.tip.attributes("-topmost", True)
        tk.Label(self.tip, text=self.text, bg="#111113", fg="white", font=(UI_FONT, -12), padx=8, pady=4).pack()
        x = self.widget.winfo_rootx() + self.widget.winfo_width() // 2 - self.tip.winfo_reqwidth() // 2
        self.tip.geometry(f"+{x}+{self.widget.winfo_rooty() + self.widget.winfo_height() + 6}")

    def _hide(self, _event=None):
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None


class Canvas(EditState):
    """tk.Canvas 를 감싼 편집 캔버스."""

    def __init__(self, editor, parent, image):
        self.editor = editor
        self.base = image.convert("RGB")
        self.widget = tk.Canvas(parent, bg=CANVAS_BG, highlightthickness=0, bd=0, takefocus=True)
        self.init_edit_state(self.base.width, self.base.height, 1.0)
        self._photo = None
        self._scaled = None  # (키, 화면 배율로 맞춘 바탕 그림)
        self._pending = None
        self._press_at = (0, 0)
        self.widget.bind("<ButtonPress-1>", self._press)
        self.widget.bind("<B1-Motion>", self._drag)
        self.widget.bind("<ButtonRelease-1>", self._release)
        self.widget.bind("<Double-Button-1>", self._double_press)
        self.widget.bind("<Motion>", lambda e: self.pointer_motion(e.x, e.y))
        self.widget.bind("<Configure>", lambda _e: self.queue_draw())

    # ── EditState 가 요구하는 것 ──────────────────────────────────────────
    def get_width(self):
        return max(self.widget.winfo_width(), 50)

    def get_height(self):
        return max(self.widget.winfo_height(), 50)

    def queue_draw(self):
        if self._pending is None:
            self._pending = self.widget.after_idle(self.redraw)

    def grab_focus(self):
        self.widget.focus_set()

    def set_cursor_from_name(self, name):
        try:
            self.widget.configure(cursor=CURSORS.get(name, "crosshair"))
        except tk.TclError:  # 그 커서가 없는 환경(X11)
            self.widget.configure(cursor="crosshair")

    def measure_text(self, shape):
        measure_text(shape)

    # ── 마우스 ────────────────────────────────────────────────────────────
    def _press(self, event):
        self._last_at = self._press_at = (event.x, event.y)
        self.pointer_press(event.x, event.y, bool(event.state & 0x1))

    def _drag(self, event):
        self._last_at = (event.x, event.y)
        self.pointer_drag(event.x, event.y, bool(event.state & 0x1))

    def _double_press(self, event):
        self._press(event)
        self.double_click(event.x, event.y)
        if self.editing:
            self.drag = None

    def _release(self, event):
        if self.drag and self.drag["mode"] != "text" and (event.x, event.y) != self._last_at:
            self._drag(event)
        self.pointer_release(event.x - self._press_at[0], event.y - self._press_at[1])

    # ── 그리기 ────────────────────────────────────────────────────────────
    def redraw(self):
        self._pending = None
        for shape in self.shapes:
            if shape.kind == "text" and shape.text_size == (0, 0):
                measure_text(shape)
        k, ox, oy = self.transform()
        view = self.view_rect()
        key = (view, round(k, 5))
        if self._scaled is None or self._scaled[0] != key:
            self._scaled = (key, scale_base(self.base, view, k))
        image = render_scene(self.base, self.scene_items(), view, k, 1 if self.drag else 3, self._scaled[1])

        crop_mode = self.tool == "crop" and self.crop_draft
        if crop_mode:  # 잘려 나갈 부분을 어둡게
            cx, cy, cw, ch = self.crop_draft
            box = (round(cx * k), round(cy * k), round((cx + cw) * k), round((cy + ch) * k))
            bright = image.crop(box)
            image = image.point(lambda v: v * 0.4)
            image.paste(bright, box[:2])

        self._photo = ImageTk.PhotoImage(image)
        canvas = self.widget
        canvas.delete("all")
        x0, y0 = ox + view[0] * k, oy + view[1] * k
        canvas.create_rectangle(x0 - 1, y0 - 1, x0 + image.width, y0 + image.height, outline="#3c3c41")
        canvas.create_image(x0, y0, anchor="nw", image=self._photo)

        if crop_mode:
            cx, cy, cw, ch = self.crop_draft
            x, y, w, h = cx * k + ox, cy * k + oy, cw * k, ch * k
            canvas.create_rectangle(x, y, x + w, y + h, outline="white", width=2)
            for fx, fy in CROP_HANDLES.values():
                hx, hy = x + w * fx, y + h * fy
                canvas.create_rectangle(hx - 5, hy - 5, hx + 5, hy + 5, fill="white", outline=ACCENT, width=2)
        elif self.selected is not None and self.selected is not (self.editing or {}).get("shape"):
            shape = self.selected
            if shape.kind not in ("arrow", "rect"):
                bx, by, bw, bh = shape.bounds()
                pad = 5 if shape.kind in ("pen", "text") else 0
                canvas.create_rectangle(
                    bx * k + ox - pad, by * k + oy - pad, (bx + bw) * k + ox + pad, (by + bh) * k + oy + pad,
                    outline=ACCENT, dash=(4, 3),
                )  # fmt: skip
            for _name, hx, hy in shape.handles():
                sx, sy = hx * k + ox, hy * k + oy
                canvas.create_oval(sx - 6, sy - 6, sx + 6, sy + 6, fill="white", outline=ACCENT, width=2)

    def export(self):
        """현재 결과물을 원본 해상도 PIL 이미지로."""
        if self.editing:
            self.commit_text()
        return render_scene(self.base, self.scene_items(final=True), self.crop, 1.0, 3)


class EditorWindow(tk.Toplevel):
    def __init__(self, root, image, on_close=None):
        super().__init__(root)
        self.title(f"{system.APP_NAME} 편집")
        self.configure(bg=CANVAS_BG)
        self._on_close = on_close
        self._syncing = False
        self._previous_tool = "arrow"
        self._text = None
        self._text_editing = None
        self._icons = {}
        self._toast_timer = None
        self._focus_timer = None
        self.saved_path = None

        self._build_header()
        self.canvas = Canvas(self, self, image)
        self.canvas.widget.pack(fill="both", expand=True)
        self._build_pill()
        self._toast = tk.Label(self, bg="#111113", fg="white", font=(UI_FONT, -13, "bold"), padx=16, pady=7)

        self._select_tool_button("arrow")
        self._select_dot(self.color_buttons, 0)
        self._select_dot(self.size_buttons, 1)
        self.sync_actions()

        self.bind("<Key>", self._key_pressed)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._set_initial_size(image)
        self._focus_timer = self.after(20, lambda: (system.bring_to_front(self), self.canvas.grab_focus()))

    # ── 구성 ──────────────────────────────────────────────────────────────
    def _icon(self, name, color=(235, 235, 240)):
        key = (name, color)
        if key not in self._icons:
            self._icons[key] = ImageTk.PhotoImage(tool_icon(name, color))
        return self._icons[key]

    def _bar_button(self, parent, icon, tooltip, command):
        button = tk.Label(parent, image=self._icon(icon), bg=BAR_BG, width=36, height=32, cursor="hand2")
        button.bind("<Button-1>", lambda _e: command())
        button.bind("<Enter>", lambda _e: button.configure(bg=BAR_HOVER) if button.cget("bg") == BAR_BG else None)
        button.bind("<Leave>", lambda _e: button.configure(bg=BAR_BG) if button.cget("bg") == BAR_HOVER else None)
        Tooltip(button, tooltip)
        return button

    def _build_header(self):
        header = tk.Frame(self, bg=BAR_BG, height=46)
        header.pack(fill="x")
        header.pack_propagate(False)

        left = tk.Frame(header, bg=BAR_BG)
        left.pack(side="left", padx=8)
        self.undo_button = self._bar_button(left, "undo", "실행 취소 (Ctrl+Z)", lambda: self.canvas.undo())
        self.redo_button = self._bar_button(left, "redo", "다시 실행 (Ctrl+Shift+Z)", lambda: self.canvas.redo())
        self.undo_button.pack(side="left")
        self.redo_button.pack(side="left")

        right = tk.Frame(header, bg=BAR_BG)
        right.pack(side="right", padx=10)
        done = tk.Label(right, text="복사하고 닫기", bg=PRIMARY, fg="white", font=(UI_FONT, -13, "bold"), padx=14, pady=6, cursor="hand2")
        done.bind("<Button-1>", lambda _e: self.copy_and_close())
        Tooltip(done, "결과를 클립보드에 복사하고 창을 닫습니다 (Enter)")
        done.pack(side="right", pady=7)
        self._bar_button(right, "save", "사진 폴더에 저장 (Ctrl+S)", self.save).pack(side="right", padx=6)

        tools = tk.Frame(header, bg=BAR_BG)
        tools.place(relx=0.5, rely=0.5, anchor="center")
        self.tool_buttons = {}
        for index, (tool, name, key) in enumerate(TOOLS, start=1):
            if tool in ("text", "blur"):
                tk.Frame(tools, bg="#4a4a50", width=1, height=22).pack(side="left", padx=6)
            button = self._bar_button(tools, tool, f"{name}  ({index} 또는 {key.upper()})", lambda t=tool: self.set_tool(t))
            button.pack(side="left", padx=1)
            self.tool_buttons[tool] = button

    def _build_pill(self):
        self.pill = tk.Frame(self, bg="#3a3a3f")
        self.pill.place(relx=0.5, rely=1.0, anchor="s", y=-18)
        self.style_row = tk.Frame(self.pill, bg=BAR_BG)
        self.style_row.pack(padx=1, pady=1)
        self.color_buttons = []
        for value, name in zip(COLORS, COLOR_NAMES):
            dot = self._dot(self.style_row, 8, value, name, lambda c=hex_rgb(value): self._pick_color(c))
            self.color_buttons.append((hex_rgb(value), dot))
        tk.Frame(self.style_row, bg="#4a4a50", width=1, height=20).pack(side="left", padx=8)
        self.size_buttons = []
        for index, radius in enumerate((2.5, 4.5, 7)):
            dot = self._dot(self.style_row, radius, "#f0f0f3", SIZE_NAMES[index], lambda i=index: self._pick_size(i))
            self.size_buttons.append((index, dot))

        self.crop_row = tk.Frame(self.pill, bg=BAR_BG)
        tk.Label(self.crop_row, text="남길 부분을 드래그로 정하세요", bg=BAR_BG, fg="white", font=(UI_FONT, -13)).pack(side="left", padx=(14, 10), pady=8)
        cancel = tk.Label(self.crop_row, text="취소", bg="#45454b", fg="white", font=(UI_FONT, -13), padx=12, pady=4, cursor="hand2")
        cancel.bind("<Button-1>", lambda _e: self.finish_crop(False))
        cancel.pack(side="left", padx=3)
        apply = tk.Label(self.crop_row, text="자르기", bg=PRIMARY, fg="white", font=(UI_FONT, -13, "bold"), padx=12, pady=4, cursor="hand2")
        apply.bind("<Button-1>", lambda _e: self.finish_crop(True))
        apply.pack(side="left", padx=(3, 10))

    def _dot(self, parent, radius, fill, tooltip, command):
        dot = tk.Canvas(parent, width=32, height=36, bg=BAR_BG, highlightthickness=0, cursor="hand2")
        dot.create_oval(16 - radius, 18 - radius, 16 + radius, 18 + radius, fill=fill, outline="#8a8a90")
        dot.bind("<Button-1>", lambda _e: command())
        dot.pack(side="left", padx=(6 if not parent.winfo_children()[:-1] else 0, 0))
        Tooltip(dot, tooltip)
        return dot

    def _select_dot(self, buttons, index):
        for i, (_value, dot) in enumerate(buttons):
            dot.delete("ring")
            if i == index:
                dot.create_oval(3, 5, 29, 31, outline="white", width=2, tags="ring")

    def _select_tool_button(self, tool):
        for name, button in self.tool_buttons.items():
            button.configure(bg=ACCENT if name == tool else BAR_BG)

    def _set_initial_size(self, image):
        screen_w, screen_h = self.winfo_screenwidth(), self.winfo_screenheight()
        width = min(max(image.width + 2 * PAD, 820), int(screen_w * 0.86))
        height = min(max(image.height + PAD_TOP + PAD_BOTTOM + 46, 540), int(screen_h * 0.84))
        self.geometry(f"{width}x{height}+{(screen_w - width) // 2}+{max(0, (screen_h - height) // 2 - 20)}")
        self.minsize(720, 420)

    # ── 도구·스타일 ───────────────────────────────────────────────────────
    def set_tool(self, tool):
        if tool != "crop":
            self._previous_tool = tool
        self.canvas.set_tool(tool)
        self._select_tool_button(tool)
        if tool == "crop":
            self.style_row.pack_forget()
            self.crop_row.pack(padx=1, pady=1)
        else:
            self.crop_row.pack_forget()
            self.style_row.pack(padx=1, pady=1)

    def finish_crop(self, apply):
        if apply:
            self.canvas.apply_crop()
        self.set_tool(self._previous_tool)

    def _pick_color(self, color):
        self._select_dot(self.color_buttons, [c for c, _d in self.color_buttons].index(color))
        self.canvas.set_color(color)

    def _pick_size(self, index):
        self._select_dot(self.size_buttons, index)
        self.canvas.set_size(index)

    def sync_style(self, shape):
        """도형을 선택하면 아래 막대가 그 도형의 색·굵기를 가리키게 한다."""
        if shape.kind != "blur":
            for i, (color, _dot) in enumerate(self.color_buttons):
                if color == shape.color:
                    self._select_dot(self.color_buttons, i)
                    self.canvas.color = color
        for index in range(3):
            current = shape.size if shape.kind == "text" else shape.width
            target = self.canvas.text_size(index) if shape.kind == "text" else self.canvas.stroke_width(index)
            if abs(current - target) < 0.01:
                self._select_dot(self.size_buttons, index)
                self.canvas.size_idx = index

    def sync_actions(self):
        canvas = getattr(self, "canvas", None)
        for button, name, stack in (
            (self.undo_button, "undo", canvas.undo_stack if canvas else []),
            (self.redo_button, "redo", canvas.redo_stack if canvas else []),
        ):
            button.configure(image=self._icon(name) if stack else self._icon(name, (110, 110, 118)))

    # ── 글자 입력 상자 ────────────────────────────────────────────────────
    def show_text_entry(self, editing):
        if self._text is None:
            self._text = tk.Text(self.canvas.widget, bd=0, highlightthickness=1, wrap="none", undo=False, padx=TEXT_PAD, pady=TEXT_PAD - 2)
            self._text.bind("<KeyRelease>", lambda _e: self._fit_text_entry())
            self._text.bind("<Return>", self._text_return)
            self._text.bind("<Escape>", lambda _e: (self.canvas.commit_text(cancel=True), "break")[1])
        self._text_editing = editing
        self._text.delete("1.0", "end")
        self._text.insert("1.0", editing["text"])
        self.style_text_entry(editing)
        self._text.focus_set()
        self._text.mark_set("insert", "end")

    def _text_return(self, event):
        if event.state & 0x1:  # Shift+Enter 는 줄바꿈
            return None
        # 한글 조합 중이면 마지막 글자가 들어온 뒤에 끝내도록 한 박자 늦춘다.
        self.after(30, self.canvas.commit_text)
        return "break"

    def style_text_entry(self, editing):
        k, _ox, _oy = self.canvas.transform()
        px = max(8, round(editing["size"] * k))
        color = "#%02x%02x%02x" % rgb(editing["color"])
        light = sum(editing["color"]) / 3 > 0.6
        self._entry_font = tkfont.Font(family=UI_FONT, size=-px, weight="bold")
        self._text.configure(
            font=self._entry_font,
            fg=color, insertbackground=color,
            bg="#2b2b30" if light else "#fbfbf6", highlightbackground=ACCENT, highlightcolor=ACCENT,
        )  # fmt: skip
        self._fit_text_entry()

    def _fit_text_entry(self):
        if self._text_editing is None:
            return
        text = self._text.get("1.0", "end-1c")
        font = self._entry_font
        lines = text.split("\n")
        px = abs(int(font.cget("size")))
        width = max(max(font.measure(line) for line in lines) + px, px * 3) + 2 * TEXT_PAD + 2
        height = len(lines) * font.metrics("linespace") + 2 * TEXT_PAD
        sx, sy = self.canvas.to_screen(*self._text_editing["at"])
        self._text.place(x=round(sx - TEXT_PAD), y=round(sy - TEXT_PAD + 2), width=width, height=height)

    def hide_text_entry(self):
        """입력 상자를 숨기고 입력된 글을 돌려준다."""
        if self._text_editing is None:
            return ""
        text = self._text.get("1.0", "end-1c")
        self._text_editing = None
        self._text.place_forget()
        return text

    # ── 키보드 ────────────────────────────────────────────────────────────
    def _key_pressed(self, event):
        canvas = self.canvas
        if canvas.editing:
            return None  # 입력 상자가 직접 처리한다
        key = event.keysym
        ctrl, shift = bool(event.state & 0x4), bool(event.state & 0x1)
        # 한글 입력 상태에서도 글자 단축키가 먹도록 키 코드(가상 키)로도 확인한다.
        letter = chr(event.keycode).lower() if system.IS_WINDOWS and 65 <= event.keycode <= 90 else key.lower()

        if ctrl:
            if letter == "z":
                canvas.redo() if shift else canvas.undo()
            elif letter == "y":
                canvas.redo()
            elif letter == "c":
                self.copy()
            elif letter == "s":
                self.save()
            else:
                return None
            return "break"
        if key in ("Return", "KP_Enter"):
            if canvas.tool == "crop":
                self.finish_crop(True)
            else:
                self.copy_and_close()
        elif key == "Escape":
            if canvas.tool == "crop":
                self.finish_crop(False)
            elif not canvas.deselect() and not canvas.dirty:
                self.close()
        elif key in ("Delete", "BackSpace"):
            canvas.delete_selected()
        else:
            for index, (tool, _name, shortcut) in enumerate(TOOLS, start=1):
                if key in (str(index), f"KP_{index}") or letter == shortcut:
                    self.set_tool(tool)
                    return "break"
            return None
        return "break"

    # ── 내보내기 ──────────────────────────────────────────────────────────
    def toast(self, text, seconds=2.2):
        self._toast.configure(text=text)
        self._toast.place(relx=0.5, y=60, anchor="n")
        self._toast.lift()
        if self._toast_timer is not None:
            self.after_cancel(self._toast_timer)
        self._toast_timer = self.after(int(seconds * 1000), self._toast.place_forget)

    def copy(self, toast=True):
        image = self.canvas.export()
        system.copy_image(image)
        self.canvas.dirty = False
        if toast:
            self.toast("클립보드에 복사했어요")
        return image

    def copy_and_close(self):
        self.copy(toast=False)
        self.close()

    def save(self):
        image = self.canvas.export()
        path = system.new_save_path()
        try:
            image.save(path, "PNG")
        except OSError as err:
            self.toast(f"저장하지 못했어요: {err.strerror}", 4)
            return None
        self.canvas.dirty = False
        self.saved_path = path
        self.toast(f"저장했어요 · {os.path.basename(path)}", 4)
        return path

    def destroy(self):
        for timer in (self._focus_timer, self._toast_timer):
            if timer is not None:
                self.after_cancel(timer)
        canvas = getattr(self, "canvas", None)
        if canvas and canvas._pending is not None:
            canvas.widget.after_cancel(canvas._pending)
        super().destroy()

    def close(self):
        self.destroy()
        if self._on_close:
            self._on_close(self)
