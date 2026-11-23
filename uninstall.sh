#!/usr/bin/env bash
# 심플찰칵 제거: install.sh 가 만든 것을 전부 되돌린다.
set -euo pipefail

APP_ID="app.chalkak.Chalkak"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}"

if gdbus call --session --dest org.freedesktop.DBus --object-path /org/freedesktop/DBus \
  --method org.freedesktop.DBus.NameHasOwner "$APP_ID" 2>/dev/null | grep -q true; then
  gapplication action "$APP_ID" quit || true
fi

if gsettings list-schemas 2>/dev/null | grep -qx org.gnome.settings-daemon.plugins.media-keys; then
  /usr/bin/python3 - <<'PY'
import ast, subprocess

SCHEMA = "org.gnome.settings-daemon.plugins.media-keys"
BASE = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/"

def gs(*args):
    return subprocess.run(["gsettings", *args], check=True, capture_output=True, text=True).stdout.strip()

raw = gs("get", SCHEMA, "custom-keybindings")
paths = ast.literal_eval(raw.replace("@as", "").strip()) if raw else []
for slug in ("chalkak-capture", "chalkak-edit"):
    path = f"{BASE}{slug}/"
    for key in ("name", "command", "binding"):
        gs("reset", f"{SCHEMA}.custom-keybinding:{path}", key)
    if path in paths:
        paths.remove(path)
gs("set", SCHEMA, "custom-keybindings", str(paths) if paths else "@as []")
PY
  if gsettings list-schemas 2>/dev/null | grep -qx org.gnome.shell.extensions.dash-to-dock; then
    gsettings reset org.gnome.shell.extensions.dash-to-dock app-shift-hotkey-2
  fi
fi

rm -rf "$DATA/chalkak" "${XDG_CACHE_HOME:-$HOME/.cache}/chalkak"
rm -f "$HOME/.local/bin/chalkak" \
  "$DATA/applications/$APP_ID.desktop" \
  "$DATA/dbus-1/services/$APP_ID.service" \
  "$DATA/icons/hicolor/scalable/apps/$APP_ID.svg" \
  "$CONFIG/autostart/$APP_ID.desktop"
update-desktop-database "$DATA/applications" >/dev/null 2>&1 || true
gdbus call --session --dest org.freedesktop.DBus --object-path /org/freedesktop/DBus \
  --method org.freedesktop.DBus.ReloadConfig >/dev/null 2>&1 || true

echo "심플찰칵을 제거했습니다. (저장해 둔 스크린샷 파일은 그대로 둡니다)"
