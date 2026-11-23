# 심플찰칵 — 리눅스용 초간단 스크린샷 + 바로 편집

`Shift + Win + 2` → 드래그 → `Enter` → **복사 끝.**
고칠 게 있으면 `Shift + Win + E` 로 편집 창을 엽니다.

캡처 화면은 `Print` 키를 눌렀을 때 나오는 GNOME 기본 스크린샷 화면 그대로입니다. 여기에 "찍은 뒤 바로 화살표·도형·글자·블러·자르기"를 붙인 앱입니다.

GNOME(Wayland) 데스크탑용. Ubuntu 26.04 · GNOME 50 · 4K 모니터 2대에서 검증했습니다.

## 설치

```bash
git clone https://github.com/dlwlsla24/screen-capture-desktop-app.git
cd screen-capture-desktop-app
./install.sh
```

- 홈 폴더 아래에만 설치합니다. `sudo`, 로그아웃, 재부팅 모두 필요 없습니다.
- 단축키 두 개가 등록되고 앱이 백그라운드에서 대기합니다. 끝에 "설치 완료"가 나오면 바로 `Shift + Win + 2` 를 눌러 보세요.
- 코드를 고친 뒤 다시 `./install.sh` 를 실행하면 새 버전으로 교체됩니다.
- 제거: `./uninstall.sh` (단축키·설정 전부 원래대로. 저장해 둔 스크린샷은 남습니다)

필요한 패키지(`python3-gi`, GTK 4.14+, libadwaita, `xdg-desktop-portal-gnome`)는 Ubuntu 기본 설치에 들어 있습니다. 빠져 있으면 설치 스크립트가 필요한 `apt` 명령을 알려 주고 멈춥니다.

## 쓰는 법

| 하고 싶은 것 | 방법 |
|---|---|
| 원하는 부분 캡처 | `Shift + Win + 2` → 드래그로 영역 지정 → `Enter` (또는 가운데 동그란 버튼) |
| 캡처 취소 | `Esc` |
| 방금 캡처 편집 | `Shift + Win + E` (알림 목록의 "방금 캡처 편집하기"를 눌러도 됩니다) |
| 찍은 파일 위치 | `~/Pictures/Screenshots/` — GNOME 이 자동 저장 |

- 캡처하면 GNOME 이 그림을 **클립보드에 복사**하고 "Screenshot captured" 알림을 띄웁니다. 바로 `Ctrl + V` 하면 됩니다.
- 캡처 화면에서 `S` 영역, `C` 화면 전체, `W` 창 하나를 고를 수 있습니다(GNOME 기본 기능).
- `Shift + Win + E` 는 **가장 최근 스크린샷**을 엽니다. `Print` 키로 찍은 것도 열립니다.

### 편집 창

위쪽 막대에서 도구를 고르고 그림 위에서 드래그하면 됩니다.

| 도구 | 단축키 | |
|---|---|---|
| 화살표 | `1` / `A` | |
| 네모 | `2` / `R` | `Shift` 를 누르면 정사각형 |
| 동그라미 | `3` / `O` | `Shift` 를 누르면 정원 |
| 세모 | `4` / `G` | |
| 펜 | `5` / `P` | 자유 곡선 |
| 텍스트 | `6` / `T` | 클릭해서 입력, `Enter` 완료, `Shift+Enter` 줄바꿈 |
| 블러 | `7` / `B` | 드래그한 영역을 뭉개서 가림 |
| 자르기 | `8` / `C` | 영역 조정 후 `Enter` |

- **도구를 바꾸지 않아도** 이미 그린 도형을 잡아 끌면 옮겨지고, 모서리 점을 끌면 크기가 바뀝니다. 텍스트는 두 번 클릭하면 다시 고칩니다.
- 아래 막대에서 색(7가지)과 굵기(3단계)를 고릅니다. 도형을 선택한 상태에서 바꾸면 그 도형에 적용됩니다.
- `Ctrl+Z` 실행 취소 · `Ctrl+Shift+Z` 다시 실행 · `Delete` 선택 도형 삭제
- **`Enter` = 복사하고 닫기** · `Ctrl+C` 복사만 · `Ctrl+S` 저장(`심플찰칵 날짜 시각.png`, 원본은 그대로) · `Esc` 선택 해제/닫기

## 단축키 바꾸기

```bash
CHALKAK_CAPTURE_KEY='<Ctrl><Alt>a' CHALKAK_EDIT_KEY='<Ctrl><Alt>e' ./install.sh
```

또는 GNOME 설정 → 키보드 → 키보드 바로 가기 → 사용자 지정 바로 가기에서 "심플찰칵" 항목을 고칩니다.

> `Shift+Win+2` 는 원래 Ubuntu Dock 의 "독 2번째 앱 새 창 열기"였습니다. 설치할 때 그 한 개를 비워 둡니다(제거하면 복구).

## 구조

화면에 보이는 이름은 "심플찰칵"이고, 폴더·명령·앱 ID 같은 내부 이름은 `chalkak` 입니다.

```
chalkak/
  app.py       백그라운드 앱. D-Bus 액션(capture, edit-last, edit)을 받는다
  capture.py   캡처 요청 (xdg-desktop-portal Screenshot, interactive=true → GNOME 스크린샷 화면)
  editor.py    편집 창 (도구 막대, 색·굵기 막대, 글자 입력, 키보드)
  canvas.py    캔버스 (그리기·옮기기·크기조절·자르기·실행취소·PNG 내보내기)
  shapes.py    도형 모델과 그리기, 히트 테스트
  icons.py     도구 아이콘 (코드로 직접 그림)
```

- 단축키는 GNOME 사용자 지정 바로 가기가 `gapplication action app.chalkak.Chalkak capture` 를 실행하는 방식입니다. 앱이 꺼져 있어도 D-Bus 가 자동으로 띄웁니다.
- 캡처·클립보드 복사·파일 저장은 GNOME Shell 이 합니다. 앱은 결과 파일 경로만 받아 편집에 씁니다.
- 편집 창의 그리기는 전부 GTK4 의 GSK(GPU) 로 합니다. 블러·도형·글자가 화면과 내보낸 PNG 에서 똑같습니다.
- 동작 기록: `journalctl --user -t app.chalkak.Chalkak -f`

### 캡처를 GNOME 화면에 맡긴 이유

처음에는 화면을 직접 받아 와 자체 선택 화면을 띄웠습니다(마우스를 놓는 순간 복사). 그런데 GNOME 은 앱이 화면을 받아 갈 때마다 **화면 전체를 하얗게 번쩍이게** 하고 이를 끌 방법이 없으며, 4K 2대에서는 선택 화면이 뜨기까지 0.7초가 걸렸습니다. GNOME 스크린샷 화면은 번쩍임 없이 약 0.2초 만에 뜹니다. 대신 드래그 뒤에 `Enter` 한 번이 필요합니다.

## 테스트

```bash
/usr/bin/python3 tests/test_render.py          # 창 없이: 그리기·블러·자르기·실행취소·히트테스트
/usr/bin/python3 tests/test_editor_window.py   # 편집 창을 1~2초 띄워 마우스·키보드 경로 13항목
/usr/bin/python3 tests/e2e_real_input.py       # 진짜 단축키+마우스(가상 입력)로 전체 흐름. 손을 떼고 실행
```

## 알아 둘 점

- 캡처할 때마다 `~/Pictures/Screenshots/` 에 파일이 하나씩 쌓입니다(`Print` 키와 같은 동작).
- GNOME Wayland 전용입니다. 다른 데스크탑에서는 캡처 화면이 다르게 뜨거나 복사가 안 될 수 있고, 단축키는 직접 등록해야 합니다.
- 실제 한글 타이핑은 아직 손으로 확인하지 않았습니다(조합 중인 마지막 글자 처리는 자동 테스트로만 확인).
