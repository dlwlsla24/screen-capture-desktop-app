"""운영체제에 물어볼 것들을 한곳에 모은다.

Windows 에서는 winapi 를 쓰고, 그 밖(개발용 리눅스)에서는 흉내만 낸다 — 편집 창 로직을
Windows 가 없는 곳에서도 테스트할 수 있게 하기 위해서다.
"""

import os
import tempfile
import sys
import time
import uuid

IS_WINDOWS = sys.platform == "win32"
APP_NAME = "심플찰칵"

last_copied = None  # 마지막으로 클립보드에 올린 PIL 이미지 (테스트 확인용)

if IS_WINDOWS:
    from . import winapi


def copy_image(image):
    global last_copied
    if IS_WINDOWS:
        import tkinter as tk

        winapi.copy_image(image, tk._default_root.winfo_id())
    else:
        last_copied = image


def virtual_screen(root):
    """(x, y, w, h) — 모든 모니터를 합친 영역."""
    if IS_WINDOWS:
        return winapi.virtual_screen()
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


def grab_screen(root):
    """모든 모니터를 한 장으로 찍는다. (image, (x, y, w, h))"""
    from PIL import ImageGrab

    x, y, w, h = virtual_screen(root)
    image = ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True)
    return image.convert("RGB"), (x, y, w, h)


def work_area(root):
    if IS_WINDOWS:
        return winapi.work_area()
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


def screenshots_dir():
    base = winapi.pictures_dir() if IS_WINDOWS else os.path.join(os.path.expanduser("~"), "Pictures")
    path = os.path.join(base, "Screenshots")
    os.makedirs(path, exist_ok=True)
    return path


def new_save_path():
    stamp = time.strftime("%Y-%m-%d %H-%M-%S")
    return os.path.join(screenshots_dir(), f"{APP_NAME} {stamp} {uuid.uuid4().hex}.png")


def save_png(image):
    while True:
        path = new_save_path()
        try:
            output = open(path, "xb")
        except FileExistsError:
            continue
        break
    try:
        with output:
            image.save(output, "PNG")
    except Exception:
        os.remove(path)
        raise
    return path


def overwrite_png(image, path):
    output = tempfile.NamedTemporaryFile(dir=os.path.dirname(path), delete=False)
    temporary = output.name
    try:
        with output:
            image.save(output, "PNG")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def reveal(path):
    """탐색기에서 그 파일을 선택한 채로 폴더를 연다."""
    if IS_WINDOWS:
        import subprocess

        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])


def bring_to_front(window):
    """단축키로 띄운 창이 키보드 입력을 받게 한다."""
    window.lift()
    window.focus_force()
    if IS_WINDOWS:
        try:
            winapi.force_foreground(int(window.wm_frame(), 16))
        except (ValueError, OSError):
            pass
