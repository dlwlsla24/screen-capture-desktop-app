# 찰칵 — 리눅스용 초간단 스크린샷 + 바로 편집

`Shift + Win + 2` → 드래그 → **끝. 이미 복사돼 있습니다.**
고칠 게 있으면 알림을 누르거나 `Shift + Win + E` 로 편집 창을 엽니다.

GNOME(Wayland) 데스크탑용. Ubuntu 26.04 · GNOME 50 · 4K 모니터 2대에서 검증했습니다.

## 설치

```bash
./install.sh
```

홈 폴더 아래에만 설치합니다(`sudo` 불필요). 설치하면 단축키 두 개가 등록되고, 앱이 백그라운드에서 대기합니다.
처음 캡처할 때 GNOME 이 **"스크린샷을 찍도록 허용할까요?"** 를 한 번 묻습니다. 허용하면 다시 묻지 않습니다.

제거는 `./uninstall.sh` (단축키·설정 전부 원래대로).

## 쓰는 법

| 하고 싶은 것 | 방법 |
|---|---|
| 원하는 부분 캡처 | `Shift + Win + 2` → 드래그. 마우스를 놓는 순간 클립보드에 복사 |
| 캡처 취소 | `Esc` 또는 오른쪽 클릭 |
| 방금 캡처 편집 | 알림 클릭, 알림의 **편집** 버튼, 또는 `Shift + Win + E` |
| 파일로 저장 | 알림의 **파일로 저장** 버튼 (편집 창에서는 `Ctrl + S`) → `~/Pictures/Screenshots/` |

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
- **`Enter` = 복사하고 닫기** · `Ctrl+C` 복사만 · `Ctrl+S` 저장 · `Esc` 선택 해제/닫기

## 단축키 바꾸기

```bash
CHALKAK_CAPTURE_KEY='<Ctrl><Alt>a' CHALKAK_EDIT_KEY='<Ctrl><Alt>e' ./install.sh
```

또는 GNOME 설정 → 키보드 → 키보드 바로 가기 → 사용자 지정 바로 가기에서 "찰칵" 항목을 고칩니다.

> `Shift+Win+2` 는 원래 Ubuntu Dock 의 "독 2번째 앱 새 창 열기"였습니다. 설치할 때 그 한 개를 비워 둡니다(제거하면 복구).

## 구조

```
chalkak/
  app.py       백그라운드 앱. D-Bus 액션(capture, edit-last, edit, save)을 받는다
  capture.py   화면 전체 한 장 (xdg-desktop-portal Screenshot, 대화상자 없음)
  overlay.py   모니터마다 전체 화면 창을 띄워 영역 선택 → 잘라서 클립보드 복사
  editor.py    편집 창 (도구 막대, 색·굵기 막대, 글자 입력, 키보드)
  canvas.py    캔버스 (그리기·옮기기·크기조절·자르기·실행취소·PNG 내보내기)
  shapes.py    도형 모델과 그리기, 히트 테스트
  icons.py     도구 아이콘 (코드로 직접 그림)
```

- 의존성: `python3-gi`, GTK 4.14+, libadwaita, `xdg-desktop-portal-gnome`. Ubuntu 기본 설치에 모두 들어 있습니다.
- 그리기는 전부 GTK4 의 GSK(GPU) 로 합니다. 블러·도형·글자가 화면과 내보낸 PNG 에서 똑같습니다.
- 단축키는 GNOME 사용자 지정 바로 가기가 `gapplication action app.chalkak.Chalkak capture` 를 실행하는 방식입니다. 앱이 꺼져 있어도 D-Bus 가 자동으로 띄웁니다.
- 동작 기록: `journalctl --user -t app.chalkak.Chalkak -f`

## 테스트

```bash
/usr/bin/python3 tests/test_render.py          # 창 없이: 그리기·블러·자르기·실행취소·히트테스트
/usr/bin/python3 tests/test_editor_window.py   # 편집 창을 1~2초 띄워 마우스·키보드 경로 13항목
/usr/bin/python3 tests/e2e_real_input.py       # 진짜 단축키+마우스(가상 입력)로 전체 흐름. 손을 떼고 실행
```

## 알아 둘 점

- **단축키를 누르고 선택 화면이 뜨기까지 4K 2대 기준 약 0.7초** 걸립니다. GNOME 이 화면 전체를 PNG 로 넘겨주는 시간이라 앱에서 줄일 수 없습니다. 모니터가 작거나 한 대면 훨씬 짧습니다.
- 선택은 한 모니터 안에서만 됩니다(두 모니터에 걸친 영역은 안 됨).
- GNOME Wayland 기준으로 만들었습니다. KDE 등 다른 데스크탑에서는 포털이 매번 허용을 물을 수 있고, 단축키는 직접 등록해야 합니다.
