"""영역 선택 화면과 캡처 직후 미리보기 (Windows 판).

단축키 → 화면 전체를 한 장 찍어 어둡게 깔고 → 드래그한 부분만 밝게 보여 준다
→ 마우스를 놓는 순간 그 부분을 잘라 돌려준다.
"""

import tkinter as tk
from tkinter import font as tkfont

from PIL import ImageEnhance, ImageTk

from . import system
from .render import UI_FONT

MIN_SIZE = 4
DIM = 0.55


class SelectionOverlay:
    def __init__(self, root, on_done, on_cancel, screenshot=None):
        """screenshot: (image, (x, y, w, h)) 를 주면 화면을 새로 찍지 않는다 (테스트용)."""
        self.on_done = on_done
        self.on_cancel = on_cancel
        self.image, (vx, vy, vw, vh) = screenshot or system.grab_screen(root)
        self.start = None
        self.end = None
        self.closed = False

        self.window = tk.Toplevel(root)
        self.window.overrideredirect(True)
        self.window.geometry(f"{vw}x{vh}+{vx}+{vy}")
        self.window.attributes("-topmost", True)
        self.canvas = tk.Canvas(self.window, width=vw, height=vh, highlightthickness=0, bd=0, cursor="crosshair", bg="black")
        self.canvas.pack(fill="both", expand=True)
        self._dark = ImageTk.PhotoImage(ImageEnhance.Brightness(self.image).enhance(DIM))
        self.canvas.create_image(0, 0, anchor="nw", image=self._dark)
        self._bright = None
        self._font = tkfont.Font(family=UI_FONT, size=-15, weight="bold")

        # 안내 문구는 주 모니터 위쪽 가운데에
        cx = root.winfo_screenwidth() / 2 - vx
        self._pill(cx, 54 - vy, "드래그해서 캡처   ·   Esc 취소", "hint", anchor="n")

        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._motion)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<ButtonPress-3>", lambda _e: self.cancel())
        self.window.bind("<Escape>", lambda _e: self.cancel())
        self.window.protocol("WM_DELETE_WINDOW", self.cancel)
        self._focus_timer = self.window.after(10, lambda: system.bring_to_front(self.window))

    def _pill(self, x, y, text, tag, anchor="n"):
        self.canvas.delete(tag)
        label = self.canvas.create_text(x, y, text=text, fill="white", font=self._font, anchor=anchor, tags=tag)
        x0, y0, x1, y1 = self.canvas.bbox(label)
        box = self.canvas.create_rectangle(x0 - 14, y0 - 7, x1 + 14, y1 + 7, fill="#151517", outline="#3a3a3f", tags=tag)
        self.canvas.tag_lower(box, label)

    # ── 입력 ──────────────────────────────────────────────────────────────
    def _press(self, event):
        self.start = self.end = (event.x, event.y)
        self.canvas.delete("hint")

    def _motion(self, event):
        if self.start is None:
            return
        x = max(0, min(self.image.width, event.x))
        y = max(0, min(self.image.height, event.y))
        self.end = (x, y)
        self._draw_selection()

    def selection(self):
        (x0, y0), (x1, y1) = self.start, self.end
        return min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)

    def _draw_selection(self):
        x, y, w, h = self.selection()
        self.canvas.delete("selection")
        if w < 1 or h < 1:
            return
        self._bright = ImageTk.PhotoImage(self.image.crop((x, y, x + w, y + h)))
        self.canvas.create_image(x, y, anchor="nw", image=self._bright, tags="selection")
        self.canvas.create_rectangle(x - 2, y - 2, x + w + 1, y + h + 1, outline="#111111", tags="selection")
        self.canvas.create_rectangle(x - 1, y - 1, x + w, y + h, outline="white", tags="selection")
        label_y = y + h + 10
        anchor = "ne"
        if label_y + 34 > self.image.height:
            label_y, anchor = max(8, y - 10), "se"
        self._pill(x + w - 14, label_y + (7 if anchor == "ne" else -7), f"{w} × {h}", "size", anchor=anchor)

    def _release(self, event):
        if self.start is None:
            return
        self._motion(event)
        x, y, w, h = self.selection()
        if w < MIN_SIZE or h < MIN_SIZE:  # 실수로 누른 것 — 다시 고르게 한다
            self.start = None
            self.canvas.delete("selection", "size")
            return
        crop = self.image.crop((x, y, x + w, y + h))
        self._close()
        self.on_done(crop)

    def cancel(self):
        if not self.closed:
            self._close()
            self.on_cancel()

    def _close(self):
        self.closed = True
        self.window.after_cancel(self._focus_timer)
        self.window.destroy()


class Toast:
    """캡처 직후 화면 오른쪽 아래에 잠깐 뜨는 미리보기. 누르면 편집 창이 열린다."""

    def __init__(self, root, image, on_click, seconds=4.0, edit_shortcut="Shift+Win+E",
                 status="저장하고 복사했어요"):
        self.window = tk.Toplevel(root)
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.configure(bg="#3a3a3f")
        body = tk.Frame(self.window, bg="#232327", cursor="hand2")
        body.pack(padx=1, pady=1)

        thumb = image.copy()
        thumb.thumbnail((240, 150))
        self._photo = ImageTk.PhotoImage(thumb)
        widgets = [body, tk.Label(body, image=self._photo, bg="#232327", bd=0)]
        widgets[1].pack(padx=10, pady=(10, 6))
        title = tk.Label(body, text=status, fg="white", bg="#232327", font=(UI_FONT, -15, "bold"))
        title.pack(padx=14, anchor="w")
        hint = tk.Label(body, text="누르면 편집" + (f"  ·  {edit_shortcut}" if edit_shortcut else ""), fg="#b9b9c0", bg="#232327", font=(UI_FONT, -13))
        hint.pack(padx=14, pady=(0, 10), anchor="w")
        widgets += [title, hint]
        for widget in widgets:
            widget.bind("<Button-1>", lambda _e: self._clicked(on_click))

        self.window.update_idletasks()
        _left, _top, right, bottom = system.work_area(root)
        width, height = self.window.winfo_reqwidth(), self.window.winfo_reqheight()
        self.window.geometry(f"+{right - width - 18}+{bottom - height - 18}")
        self._timer = self.window.after(int(seconds * 1000), self.close)

    def _clicked(self, on_click):
        self.close()
        on_click()

    def close(self):
        if self.window is not None:
            try:
                self.window.after_cancel(self._timer)
                self.window.destroy()
            except tk.TclError:
                pass
            self.window = None
