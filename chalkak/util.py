"""공용 헬퍼: 경로, 색, 좌표 타입, 클립보드."""

import os
import sys
import time
import warnings

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Graphene  # noqa: E402

APP_ID = "app.chalkak.Chalkak"
APP_NAME = "찰칵"


def log(*parts):
    """journalctl --user -t chalkak 로 볼 수 있는 간단한 동작 기록."""
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
def cache_dir():
    path = os.path.join(GLib.get_user_cache_dir(), "chalkak")
    os.makedirs(path, exist_ok=True)
    return path


def pictures_dir():
    return GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES) or os.path.expanduser("~")


def screenshots_dir():
    path = os.path.join(pictures_dir(), "Screenshots")
    os.makedirs(path, exist_ok=True)
    return path


def new_save_path():
    """~/Pictures/Screenshots/찰칵 2026-11-23 14-30-12.png (겹치면 번호를 붙인다)."""
    stamp = time.strftime("%Y-%m-%d %H-%M-%S")
    base = os.path.join(screenshots_dir(), f"찰칵 {stamp}")
    path, n = base + ".png", 1
    while os.path.exists(path):
        n += 1
        path = f"{base} ({n}).png"
    return path


def prune_cache(keep=12):
    """캐시에 최근 캡처 몇 장만 남긴다(알림의 '편집' 버튼용)."""
    folder = cache_dir()
    files = sorted(
        (f for f in os.listdir(folder) if f.startswith("capture-") and f.endswith(".png")),
        reverse=True,
    )
    for name in files[keep:]:
        try:
            os.remove(os.path.join(folder, name))
        except OSError:
            pass


# ── 이미지 ─────────────────────────────────────────────────────────────────
def texture_from_pixbuf(pixbuf):
    """픽셀을 복사하지 않고 텍스처로 감싼다 (4K 화면 두 장이면 복사만 0.1초가 든다)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return Gdk.Texture.new_for_pixbuf(pixbuf)


def pixbuf_to_png(pixbuf):
    ok, data = pixbuf.save_to_bufferv("png", ["compression"], ["3"])
    if not ok:
        raise RuntimeError("PNG 인코딩 실패")
    return bytes(data)


def load_pixbuf(path):
    return GdkPixbuf.Pixbuf.new_from_file(path)


# ── 클립보드 ───────────────────────────────────────────────────────────────
def copy_png(display, png_bytes):
    """PNG 바이트를 클립보드에 올린다.

    Wayland 에서는 우리 창이 키보드 포커스를 가진 순간에만 받아들여지므로
    반드시 창이 떠 있는 상태에서 호출한다.
    """
    provider = Gdk.ContentProvider.new_for_bytes("image/png", GLib.Bytes.new(png_bytes))
    display.get_clipboard().set_content(provider)
