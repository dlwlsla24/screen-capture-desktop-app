"""Windows frontend regression suite. Use a disposable desktop for native clipboard checks.

Pointer gestures are sent to Tk widgets. No mouse or keyboard device input is generated.
"""

import sys
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from tkinter import font as tkfont
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image, ImageChops, ImageDraw, ImageGrab

from chalkak.win import system
from chalkak.win.app import WindowsApp
from chalkak.win.editor import EditorWindow
from chalkak.win.overlay import SelectionOverlay


def pattern():
    image = Image.new("RGB", (240, 180), "#223344")
    draw = ImageDraw.Draw(image)
    for x in range(0, image.width, 2):
        draw.line((x, 0, x, image.height), fill="#e0e4ee")
    draw.rectangle((6, 9, 13, 16), fill="blue")
    return image


class WindowTests(unittest.TestCase):
    def setUp(self):
        if sys.platform == "win32":
            system.winapi.set_dpi_aware()
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.errors = []
        self.root.report_callback_exception = lambda _cls, err, _tb: self.errors.append(err)
        self.image = pattern()

    def tearDown(self):
        self.assertEqual(self.errors, [], "an event callback failed")

    def editor(self, image=None):
        editor = EditorWindow(self.root, self.image if image is None else image)
        self.root.update()
        return editor

    def show_overlay(self, on_done, on_cancel):
        overlay = SelectionOverlay(self.root, on_done, on_cancel,
                                   (self.image, (0, 0, *self.image.size)))
        ready = tk.BooleanVar(self.root)
        overlay.window.after(15, lambda: ready.set(True))
        self.root.wait_variable(ready)
        return overlay

    def gesture(self, editor, start, end, shift=False):
        canvas = editor.canvas
        x, y = map(round, canvas.to_screen(*start))
        canvas.widget.event_generate("<ButtonPress-1>", x=x, y=y)
        x, y = map(round, canvas.to_screen(*end))
        # Tk can coalesce fast motion. Release is the definitive endpoint.
        canvas.widget.event_generate("<ButtonRelease-1>", x=x, y=y, state=int(shift))
        self.root.update()

    def test_overlay_selection_and_editor(self):
        selected, cancelled = [], []
        overlay = self.show_overlay(selected.append, lambda: cancelled.append(True))
        canvas = overlay.canvas
        canvas.event_generate("<ButtonPress-1>", x=40, y=60)
        canvas.event_generate("<ButtonRelease-1>", x=42, y=62)
        self.assertEqual(selected, [])
        self.assertFalse(overlay.closed)
        canvas.event_generate("<ButtonPress-1>", x=110, y=120)
        canvas.event_generate("<B1-Motion>", x=10, y=20)
        self.assertEqual(overlay.selection(), (10, 20, 100, 100))
        canvas.event_generate("<ButtonRelease-1>", x=4, y=9)
        self.assertTrue(overlay.closed)
        self.assertEqual(cancelled, [])
        edited = self.editor(selected.pop()).canvas.export()
        expected = self.image.crop((4, 9, 110, 120))
        self.assertEqual((edited.size, edited.tobytes()), (expected.size, expected.tobytes()))

    def test_overlay_cancel(self):
        cancelled = []
        overlay = self.show_overlay(self.fail, lambda: cancelled.append(True))
        overlay.canvas.event_generate("<ButtonPress-3>")
        overlay.cancel()
        self.assertEqual(cancelled, [True])

    def test_capture_preview_opens_latest_selection(self):
        app = WindowsApp(self.root)
        app.hotkeys = SimpleNamespace(labels={"edit": "Shift+Win+E"})
        with patch.object(system, "grab_screen", return_value=(self.image, (0, 0, *self.image.size))), \
             patch.object(system, "copy_image") as copy:
            app.capture()
            overlay = app.overlay
            ready = tk.BooleanVar(self.root)
            overlay.window.after(15, lambda: ready.set(True))
            self.root.wait_variable(ready)
            overlay.canvas.event_generate("<ButtonPress-1>", x=22, y=16)
            overlay.canvas.event_generate("<ButtonRelease-1>", x=122, y=96)
            copy.assert_called_once_with(app.last_image)
            self.assertEqual(app.last_image.tobytes(), self.image.crop((22, 16, 122, 96)).tobytes())
            self.assertIsNone(app.overlay)
            self.root.update()
            body = app.preview.window.winfo_children()[0]
            body.event_generate("<Button-1>", x=4, y=4)
            self.assertIsNone(app.preview)
            self.assertEqual(app.editor.canvas.export().tobytes(), app.last_image.tobytes())
            app.editor.close()
            self.assertIsNone(app.editor)

    def test_draw_tools(self):
        for tool in ("arrow", "rect", "ellipse", "triangle", "pen", "blur"):
            with self.subTest(tool=tool):
                editor = self.editor()
                editor.set_tool(tool)
                self.gesture(editor, (30, 40), (180, 130))
                canvas = editor.canvas
                self.assertEqual([shape.kind for shape in canvas.shapes], [tool])
                result = canvas.export()
                bounds = ImageChops.difference(result, self.image).getbbox()
                self.assertIsNotNone(bounds)
                self.assertLess(bounds[0], 40)
                self.assertGreater(bounds[2], 170)
                self.assertEqual(result.getpixel((5, 5)), self.image.getpixel((5, 5)))
                if tool == "blur":
                    channel = result.crop((75, 60, 125, 100)).getchannel("R")
                    lo, hi = channel.getextrema()
                    self.assertLess(hi - lo, 16)
                editor.close()

    def test_crop_preserves_last_column_of_blur(self):
        editor = self.editor()
        canvas = editor.canvas
        editor.set_tool("blur")
        self.gesture(editor, (30, 40), (180, 130))
        full = canvas.export()
        self.assertNotEqual(full.getpixel((179, 90)), self.image.getpixel((179, 90)))
        editor.set_tool("crop")
        self.gesture(editor, (0, 0), (179, 50))
        editor.finish_crop(True)
        self.assertEqual(canvas.export().tobytes(), full.crop((179, 50, 240, 180)).tobytes())

    def test_shape_edit_crop_save_and_history(self):
        editor = self.editor()
        canvas = editor.canvas
        editor.set_tool("rect")
        self.gesture(editor, (20, 20), (80, 50), shift=True)
        self.assertEqual(canvas.selected.bounds(), (20, 20, 60, 60))
        self.gesture(editor, (20, 50), (40, 65))
        self.assertEqual(canvas.selected.bounds(), (40, 35, 60, 60))
        self.gesture(editor, (100, 95), (120, 110))
        self.assertEqual(canvas.selected.bounds(), (40, 35, 80, 75))
        editor._pick_color((1, 1, 1))
        full = canvas.export()
        self.assertEqual(full.getpixel((40, 60)), (255, 255, 255))
        editor.set_tool("crop")
        self.gesture(editor, (0, 0), (25, 17))
        editor.finish_crop(False)
        self.assertEqual(canvas.export().size, self.image.size)
        editor.set_tool("crop")
        self.gesture(editor, (0, 0), (25, 17))
        editor.finish_crop(True)
        expected = full.crop((25, 17, 240, 180))
        self.assertEqual(canvas.tool, "rect")
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "result.png")
            with patch.object(system, "new_save_path", return_value=path):
                self.assertEqual(editor.save(), path)
            with Image.open(path) as saved:
                self.assertEqual((saved.size, saved.tobytes()), (expected.size, expected.tobytes()))
        canvas.undo()
        self.assertEqual(canvas.export().tobytes(), full.tobytes())
        canvas.redo()
        self.assertEqual(canvas.export().size, expected.size)

    def test_text_font_commit_edit_and_cancel(self):
        editor = self.editor()
        editor.set_tool("text")
        self.gesture(editor, (20, 20), (20, 20))
        field = editor._text
        font = tkfont.nametofont(field.cget("font"))
        self.assertEqual(font.cget("weight"), "bold")
        self.assertEqual(int(font.cget("size")), -round(editor.canvas.text_size() * editor.canvas.transform()[0]))
        field.insert("1.0", "한글\nABC")
        rendered = editor.canvas.export()
        shape = editor.canvas.selected
        self.assertEqual(shape.text, "한글\nABC")
        self.assertGreater(shape.text_size[1], shape.size)
        self.assertIsNotNone(ImageChops.difference(rendered, self.image).getbbox())
        editor.canvas.double_click(*editor.canvas.to_screen(24, 24))
        field.insert("end", " changed")
        editor.canvas.commit_text(cancel=True)
        self.assertEqual(shape.text, "한글\nABC")

    @unittest.skipUnless(sys.platform == "win32", "requires Windows desktop capture")
    def test_native_screen_capture(self):
        marker = tk.Toplevel(self.root)
        marker.overrideredirect(True)
        marker.attributes("-topmost", True)
        marker.geometry("80x60+20+20")
        marker.configure(bg="#4791c3")
        self.root.update()
        x, y = marker.winfo_rootx() + 40, marker.winfo_rooty() + 30
        # Give Desktop Window Manager time to present the mapped Tk surface.
        import time
        deadline = time.monotonic() + 3
        while True:
            image, (vx, vy, w, h) = system.grab_screen(self.root)
            self.assertEqual(image.size, (w, h))
            pixel = image.getpixel((x - vx, y - vy))
            if pixel == (71, 145, 195) or time.monotonic() >= deadline:
                break
            self.root.update()
            time.sleep(0.05)
        self.assertEqual(pixel, (71, 145, 195))

    @unittest.skipUnless(sys.platform == "win32", "requires Windows clipboard")
    def test_native_clipboard_after_close(self):
        editor = self.editor()
        editor.set_tool("rect")
        self.gesture(editor, (10, 10), (80, 80))
        expected = editor.canvas.export()
        editor.copy_and_close()
        api = system.winapi.user32
        for fmt in [8, api.RegisterClipboardFormatW("PNG")]:
            self.assertTrue(api.IsClipboardFormatAvailable(fmt))
        pasted = ImageGrab.grabclipboard()
        self.assertIsInstance(pasted, Image.Image)
        self.assertEqual(pasted.size, expected.size)
        self.assertEqual(pasted.convert("RGB").tobytes(), expected.tobytes())


if __name__ == "__main__":
    unittest.main()
