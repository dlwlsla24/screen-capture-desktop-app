"""공용 헬퍼: 경로, 색, 좌표 타입, 클립보드."""

import os
import stat
import sys
import tempfile
import time
import warnings

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Graphene  # noqa: E402

APP_ID = "app.chalkak.Chalkak"
APP_NAME = "심플찰칵"


def log(*parts):
    """journalctl --user -t app.chalkak.Chalkak 로 볼 수 있는 간단한 동작 기록."""
    print(time.strftime("%H:%M:%S") + f".{int(time.time() * 1000) % 1000:03d}", *parts, file=sys.stderr, flush=True)


# ── 좌표·색 ────────────────────────────────────────────────────────────────
def rect(x, y, w, h):
    return Graphene.Rect().init(x, y, w, h)


def point(x, y):
    return Graphene.Point().init(x, y)


def rgba(r, g, b, a=1.0):
    c = Gdk.RGBA()
    c.red, c.green, c.blue, c.alpha = r, g, b, a
    return c


def hex_rgb(value):
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) / 255 for i in (0, 2, 4))


# ── 경로 ───────────────────────────────────────────────────────────────────
def pictures_dir():
    return GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES) or os.path.expanduser("~")


def screenshots_dir():
    path = os.path.join(pictures_dir(), "Screenshots")
    os.makedirs(path, exist_ok=True)
    return path


def save_png(png):
    """완성된 PNG만 공개하고 같은 시각의 다른 파일은 덮어쓰지 않는다."""
    folder = screenshots_dir()
    stamp = time.strftime("%Y-%m-%d %H-%M-%S")
    base = os.path.join(folder, f"심플찰칵 {stamp}")
    with tempfile.NamedTemporaryFile(dir=folder, prefix=".chalkak-") as staging:
        staging.write(png)
        staging.flush()
        os.fsync(staging.fileno())
        number = 1
        while True:
            suffix = "" if number == 1 else f" ({number})"
            path = f"{base}{suffix}.png"
            try:
                os.link(staging.name, path)
                return path
            except FileExistsError:
                number += 1


def replace_capture_png(path, png):
    """Publish the edit atomically while retaining the capture time used by edit-last."""
    original = os.stat(path)
    with tempfile.NamedTemporaryFile(dir=os.path.dirname(path), prefix=".chalkak-", delete=False) as staging:
        try:
            staging.write(png)
            staging.flush()
            os.fchmod(staging.fileno(), stat.S_IMODE(original.st_mode))
            os.utime(staging.fileno(), ns=(original.st_atime_ns, original.st_mtime_ns))
            os.fsync(staging.fileno())
            os.replace(staging.name, path)
        finally:
            if os.path.exists(staging.name):
                os.unlink(staging.name)


def latest_screenshot():
    """스크린샷 폴더에서 가장 최근 PNG. (Print 키로 찍은 것도 포함된다.)"""
    folders = {screenshots_dir(), os.path.join(pictures_dir(), "스크린샷")}
    newest, newest_time = None, 0.0
    for folder in folders:
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        for name in names:
            if not name.lower().endswith(".png"):
                continue
            path = os.path.join(folder, name)
            try:
                modified = os.stat(path).st_mtime_ns
            except OSError:
                continue
            if modified > newest_time:
                newest, newest_time = path, modified
    return newest


# ── 이미지 ─────────────────────────────────────────────────────────────────
def texture_from_pixbuf(pixbuf):
    """픽셀을 복사하지 않고 텍스처로 감싼다 (4K 화면 두 장이면 복사만 0.1초가 든다)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return Gdk.Texture.new_for_pixbuf(pixbuf)


def load_pixbuf(path):
    return GdkPixbuf.Pixbuf.new_from_file(path)


# ── 클립보드 ───────────────────────────────────────────────────────────────
def copy_png(display, png_bytes):
    """PNG 바이트를 클립보드에 올린다.

    Wayland 에서는 우리 창이 키보드 포커스를 가진 순간에만 받아들여지므로
    반드시 창이 떠 있는 상태에서 호출한다.
    """
    provider = Gdk.ContentProvider.new_for_bytes("image/png", GLib.Bytes.new(png_bytes))
    if not display.get_clipboard().set_content(provider):
        raise OSError("클립보드가 이미지를 받지 못했습니다")
