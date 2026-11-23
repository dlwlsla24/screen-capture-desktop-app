"""설치된 찰칵을 진짜 단축키·마우스로 끝까지 돌려 본다 (GNOME 전용, 개발용).

Mutter RemoteDesktop 으로 가상 입력을 넣는다. 실행하는 약 8초 동안 실제 키보드·마우스가
움직이므로 손을 떼고 있어야 한다.
  Shift+Super+2 → 드래그 → (알림 확인용 화면 저장) → Shift+Super+E → (편집 창 화면 저장) → Esc

실행: /usr/bin/python3 tests/e2e_real_input.py /tmp/chalkak-e2e
결과: 새 캡처 파일 경로 출력 + <접두어>-notify.png / -editor.png / -after.png
"""
import glob, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gi
from gi.repository import Gio, GLib
from chalkak.capture import ScreenGrab
OUT = sys.argv[1] if len(sys.argv) > 1 else '/tmp/chalkak-e2e'
CACHE = os.path.join(GLib.get_user_cache_dir(), 'chalkak')
bus = Gio.bus_get_sync(Gio.BusType.SESSION)
RD = 'org.gnome.Mutter.RemoteDesktop'
def call(path, iface, method, args=None, reply=None):
    return bus.call_sync(RD, path, iface, method, args, reply, 0, -1, None)
session = call('/org/gnome/Mutter/RemoteDesktop', RD, 'CreateSession', None, GLib.VariantType('(o)')).unpack()[0]
SI = RD + '.Session'
call(session, SI, 'Start')
def key(code, down): call(session, SI, 'NotifyKeyboardKeycode', GLib.Variant('(ub)', (code, down)))
def button(down): call(session, SI, 'NotifyPointerButton', GLib.Variant('(ib)', (272, down)))
def move(dx, dy): call(session, SI, 'NotifyPointerMotionRelative', GLib.Variant('(dd)', (dx, dy)))
def chord(*codes):
    for c in codes: key(c, True); time.sleep(0.03)
    for c in reversed(codes): key(c, False); time.sleep(0.03)
SHIFT, SUPER, K2, KE, ESC = 42, 125, 3, 18, 1
loop = GLib.MainLoop()
def grab(name, then):
    def done(pb):
        pb.savev(f'{OUT}-{name}.png', 'png', [], []); print('grab', name, flush=True); then()
    g = ScreenGrab(done, lambda m: (print('grab err', m), then())); g.start(); grab.keep = g
def files(): return sorted(glob.glob(CACHE + '/capture-*.png'))
before = files()
t0 = time.time()
def step1():
    chord(SHIFT, SUPER, K2); print(time.strftime('%H:%M:%S'), 'hotkey capture sent', flush=True)
    GLib.timeout_add(1600, step2); return False
def step2():
    button(True); time.sleep(0.05)
    for _ in range(12): move(40, 25); time.sleep(0.02)
    time.sleep(0.15); button(False); print(time.strftime('%H:%M:%S'), 'drag done', flush=True)
    GLib.timeout_add(1300, step3); return False
def step3():
    new = [f for f in files() if f not in before]; print('new capture files:', new, flush=True)
    grab('notify', lambda: GLib.timeout_add(300, step4)); return False
def step4():
    chord(SHIFT, SUPER, KE); print('hotkey edit sent', flush=True)
    GLib.timeout_add(1500, lambda: grab('editor', lambda: GLib.timeout_add(200, step5)) and False); return False
def step5():
    chord(ESC); print('esc sent', flush=True)
    GLib.timeout_add(900, lambda: grab('after', finish) and False); return False
def finish():
    call(session, SI, 'Stop'); loop.quit()
GLib.timeout_add(300, step1)
GLib.timeout_add_seconds(25, lambda: (print('TIMEOUT'), finish()))
loop.run()
