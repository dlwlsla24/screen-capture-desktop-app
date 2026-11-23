#!/usr/bin/env bash
# 찰칵 설치: 사용자 홈 아래에만 설치한다 (sudo 불필요).
#   - 앱 본체        ~/.local/share/chalkak/
#   - 실행 파일      ~/.local/bin/chalkak
#   - 앱 목록 등록   ~/.local/share/applications/app.chalkak.Chalkak.desktop
#   - D-Bus 자동 실행 ~/.local/share/dbus-1/services/app.chalkak.Chalkak.service
#   - 로그인 시 대기 ~/.config/autostart/app.chalkak.Chalkak.desktop
#   - 단축키         Shift+Super+2 (캡처), Shift+Super+E (방금 캡처 편집)
set -euo pipefail

APP_ID="app.chalkak.Chalkak"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}"
BIN="$HOME/.local/bin"
PYTHON="/usr/bin/python3"
CAPTURE_KEY="${CHALKAK_CAPTURE_KEY:-<Shift><Super>2}"
EDIT_KEY="${CHALKAK_EDIT_KEY:-<Shift><Super>e}"

say() { printf '\033[1m▸ %s\033[0m\n' "$*"; }

# ── 0. 필요한 것 확인 ──────────────────────────────────────────────────────
"$PYTHON" - <<'PY' || { echo "필요한 패키지가 없습니다: sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 xdg-desktop-portal-gnome"; exit 1; }
import gi
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1"); gi.require_version("Gsk", "4.0")
from gi.repository import Gtk, Adw, Gsk
assert (Gtk.get_major_version(), Gtk.get_minor_version()) >= (4, 14), "GTK 4.14 이상이 필요합니다"
PY

# ── 1. 실행 중이면 끈다 ────────────────────────────────────────────────────
running() {
  gdbus call --session --dest org.freedesktop.DBus --object-path /org/freedesktop/DBus \
    --method org.freedesktop.DBus.NameHasOwner "$APP_ID" 2>/dev/null | grep -q true
}
if running; then
  say "실행 중인 찰칵을 종료합니다"
  gapplication action "$APP_ID" quit || true
  sleep 0.5
fi

# ── 2. 파일 복사 ───────────────────────────────────────────────────────────
say "앱 파일을 설치합니다 → $DATA/chalkak"
rm -rf "$DATA/chalkak/chalkak"
mkdir -p "$DATA/chalkak/chalkak" "$BIN" "$DATA/applications" "$DATA/dbus-1/services" \
  "$DATA/icons/hicolor/scalable/apps" "$CONFIG/autostart"
cp "$SRC"/chalkak/*.py "$DATA/chalkak/chalkak/"
cp "$SRC/data/$APP_ID.svg" "$DATA/icons/hicolor/scalable/apps/$APP_ID.svg"

cat > "$BIN/chalkak" <<EOF
#!$PYTHON
import sys
sys.path.insert(0, "$DATA/chalkak")
from chalkak.app import main
sys.exit(main())
EOF
chmod +x "$BIN/chalkak"

cat > "$DATA/applications/$APP_ID.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=찰칵
GenericName=스크린샷
Comment=영역을 드래그해 캡처하고 바로 복사·편집
Exec=$BIN/chalkak
Icon=$APP_ID
Terminal=false
DBusActivatable=true
StartupNotify=false
Categories=Utility;Graphics;
Keywords=screenshot;capture;스크린샷;캡처;캡쳐;찰칵;
Actions=capture;edit-last;

[Desktop Action capture]
Name=새 캡처
Exec=gapplication action $APP_ID capture

[Desktop Action edit-last]
Name=방금 캡처 편집
Exec=gapplication action $APP_ID edit-last
EOF

cat > "$DATA/dbus-1/services/$APP_ID.service" <<EOF
[D-BUS Service]
Name=$APP_ID
Exec=$BIN/chalkak --gapplication-service
EOF

cat > "$CONFIG/autostart/$APP_ID.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=찰칵 (대기)
Comment=단축키에 바로 반응하도록 로그인할 때 미리 띄워 둡니다
Exec=gdbus call --session --dest org.freedesktop.DBus --object-path /org/freedesktop/DBus --method org.freedesktop.DBus.StartServiceByName $APP_ID 0
Icon=$APP_ID
NoDisplay=true
X-GNOME-Autostart-enabled=true
EOF

update-desktop-database "$DATA/applications" >/dev/null 2>&1 || true
gtk-update-icon-cache -q -t "$DATA/icons/hicolor" >/dev/null 2>&1 || true
# D-Bus 가 새 서비스 파일을 읽게 한다
gdbus call --session --dest org.freedesktop.DBus --object-path /org/freedesktop/DBus \
  --method org.freedesktop.DBus.ReloadConfig >/dev/null 2>&1 || true

# ── 3. 단축키 등록 (GNOME) ─────────────────────────────────────────────────
if gsettings list-schemas 2>/dev/null | grep -qx org.gnome.settings-daemon.plugins.media-keys; then
  say "단축키를 등록합니다: 캡처 $CAPTURE_KEY · 편집 $EDIT_KEY"
  "$PYTHON" - "$APP_ID" "$CAPTURE_KEY" "$EDIT_KEY" <<'PY'
import ast, subprocess, sys

app_id, capture_key, edit_key = sys.argv[1:4]
SCHEMA = "org.gnome.settings-daemon.plugins.media-keys"
BASE = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/"

def gs(*args):
    return subprocess.run(["gsettings", *args], check=True, capture_output=True, text=True).stdout.strip()

raw = gs("get", SCHEMA, "custom-keybindings")
paths = ast.literal_eval(raw.replace("@as", "").strip()) if raw else []
entries = [
    ("chalkak-capture", "찰칵: 영역 캡처", f"gapplication action {app_id} capture", capture_key),
    ("chalkak-edit", "찰칵: 방금 캡처 편집", f"gapplication action {app_id} edit-last", edit_key),
]
for slug, name, command, binding in entries:
    path = f"{BASE}{slug}/"
    target = f"{SCHEMA}.custom-keybinding:{path}"
    gs("set", target, "name", name)
    gs("set", target, "command", command)
    gs("set", target, "binding", binding)
    if path not in paths:
        paths.append(path)
gs("set", SCHEMA, "custom-keybindings", str(paths))
PY
  # Ubuntu Dock 이 Shift+Super+숫자를 "독의 N번째 앱 새 창"으로 쓰고 있으면 비켜 준다.
  if [ "$CAPTURE_KEY" = "<Shift><Super>2" ] && \
     gsettings list-schemas 2>/dev/null | grep -qx org.gnome.shell.extensions.dash-to-dock; then
    gsettings set org.gnome.shell.extensions.dash-to-dock app-shift-hotkey-2 "[]"
  fi
else
  echo "GNOME 이 아니어서 단축키는 직접 등록해야 합니다: gapplication action $APP_ID capture"
fi

# ── 4. 대기 상태로 띄운다 ──────────────────────────────────────────────────
gdbus call --session --dest org.freedesktop.DBus --object-path /org/freedesktop/DBus \
  --method org.freedesktop.DBus.StartServiceByName "$APP_ID" 0 >/dev/null 2>&1 || \
  (setsid "$BIN/chalkak" --gapplication-service >/dev/null 2>&1 &)

say "설치 완료. Shift+Super(Win)+2 를 눌러 보세요."
