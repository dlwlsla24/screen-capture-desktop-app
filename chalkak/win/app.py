"""Windows tray and Tk event loop for 심플찰칵."""

import queue
import threading
import tkinter as tk
from tkinter import messagebox

from PIL import Image, ImageDraw

from . import system
from .editor import EditorWindow
from .overlay import SelectionOverlay, Toast


def tray_image():
    image = Image.new("RGBA", (64, 64))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((2, 12, 62, 54), radius=10, fill="#2064ce")
    draw.rounded_rectangle((12, 6, 30, 18), radius=4, fill="#2064ce")
    draw.ellipse((19, 18, 46, 45), outline="white", width=5)
    return image


class WindowsApp:
    def __init__(self, root):
        self.root = root
        self.events = queue.Queue()
        self.last_path = None
        self.editor = None
        self.open_editors = []
        self.overlay = None
        self.preview = None
        self.icon = None
        self.hotkeys = None
        self.closing = False

    def create_icon(self, pystray):
        item = pystray.MenuItem

        def action(name):
            def callback(icon, menu_item):
                self.events.put(name)
            return callback

        def label(name, title):
            shortcut = self.hotkeys.labels.get(name)
            return f"{title} ({shortcut})" if shortcut else title

        return pystray.Icon("SimpleChalkak", tray_image(), system.APP_NAME, pystray.Menu(
            item(label("capture", "영역 캡처"), action("capture"), default=True),
            item(label("edit", "최근 캡처 편집"), action("edit")),
            pystray.Menu.SEPARATOR,
            item("로그인할 때 자동 실행", action("startup"), checked=lambda _: self.autostart),
            item("종료", action("exit")),
        ))

    def run(self, pystray, winapi):
        self.root.withdraw()
        self.root.report_callback_exception = self.callback_error
        self.autostart = winapi.autostart_enabled()
        self.hotkeys = winapi.Hotkeys(self.events.put)
        self.hotkeys.start()
        try:
            self.hotkeys.ready.wait()
            self.icon = self.create_icon(pystray)
            tray = threading.Thread(target=self.run_tray, daemon=True, name="chalkak-tray")
            tray.start()
            if len(self.hotkeys.labels) < 2:
                self.events.put(("error", "일부 단축키를 등록할 수 없어요. 트레이 메뉴에서 실행하세요."))
            self.root.after(40, self.poll)
            self.root.mainloop()
        finally:
            self.closing = True
            self.hotkeys.stop()
            if self.icon:
                self.icon.stop()

    def run_tray(self):
        try:
            self.icon.run()
        except Exception as exc:
            self.events.put(("error", f"트레이를 시작할 수 없어요. {exc}"))
        finally:
            self.events.put("exit")

    def callback_error(self, exc_type, exc, traceback):
        messagebox.showerror(system.APP_NAME, str(exc), parent=self.root)

    def poll(self):
        try:
            while not self.events.empty():
                event = self.events.get_nowait()
                if event == "exit":
                    for editor in self.open_editors.copy():
                        if not editor.close():
                            system.bring_to_front(editor)
                            return
                    self.closing = True
                    self.root.quit()
                    return
                if event == "capture":
                    self.capture()
                elif event == "edit":
                    self.edit_last()
                elif event == "startup":
                    from . import winapi
                    winapi.set_autostart(not self.autostart)
                    self.autostart = winapi.autostart_enabled()
                    self.icon.update_menu()
                elif isinstance(event, tuple) and event[0] == "error":
                    messagebox.showerror(system.APP_NAME, event[1], parent=self.root)
        finally:
            if not self.closing:
                self.root.after(40, self.poll)

    def dismiss_preview(self):
        if self.preview:
            self.preview.close()
            self.preview = None

    def capture(self):
        if self.overlay:
            system.bring_to_front(self.overlay.window)
            return
        self.dismiss_preview()
        self.root.update_idletasks()
        self.overlay = SelectionOverlay(self.root, self.captured, self.cancelled)

    def cancelled(self):
        self.overlay = None

    def captured(self, image):
        self.overlay = None
        try:
            path = system.save_png(image)
        except OSError as err:
            messagebox.showerror(system.APP_NAME, f"캡처를 저장하지 못했어요: {err}", parent=self.root)
            return
        self.last_path = path
        self.editor = None  # Earlier open editors keep their own image and edits.
        try:
            system.copy_image(image)
            status = "저장하고 복사했어요"
        except OSError:
            status = "PNG 저장 완료 · 복사 실패"
        self.preview = Toast(self.root, image, self.edit_last,
                             edit_shortcut=self.hotkeys.labels.get("edit"), status=status)

    def edit_last(self):
        if self.overlay:
            return
        self.dismiss_preview()
        if self.last_path is None:
            messagebox.showinfo(system.APP_NAME, "먼저 영역을 캡처하세요.", parent=self.root)
        elif self.editor is None:
            try:
                with Image.open(self.last_path) as saved:
                    image = saved.convert("RGB")
            except OSError as err:
                messagebox.showerror(system.APP_NAME, f"캡처를 열지 못했어요: {err}", parent=self.root)
                return
            self.editor = EditorWindow(self.root, image, self.editor_closed, saved_path=self.last_path)
            self.open_editors.append(self.editor)
        else:
            self.editor.deiconify()
            system.bring_to_front(self.editor)

    def editor_closed(self, editor):
        self.open_editors.remove(editor)
        if editor is self.editor:
            self.editor = None


def main():
    from . import winapi

    root = None
    try:
        winapi.set_dpi_aware()
        if not winapi.single_instance():
            return 0
        root = tk.Tk()
        root.withdraw()
        import pystray

        WindowsApp(root).run(pystray, winapi)
    except Exception as exc:
        messagebox.showerror(system.APP_NAME, str(exc), parent=root)
        return 1
    finally:
        if root is not None:
            root.destroy()
    return 0
