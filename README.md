# MFWSide

MaaFramework와 PySide6 기반 Windows용 공용 실행 UI입니다. 앱 코드와 게임별 리소스를 분리합니다.

## 소스 실행

Windows x64 / Python 3.10 환경에서 저장소 루트에서 실행합니다.

```powershell
py -3.10 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe main.py
```

가상환경은 새로 생성합니다. 다른 프로젝트의 `.venv`는 복사하지 않습니다.

## 앱과 리소스의 경계

```text
MFWSide/
  main.py                   앱 진입점
  app/                      UI, 런타임, 아이콘
  UnitTest/                 앱 회귀 검사와 합성 fixture
  assets/                   별도 설치하는 외부 데이터 (Git 제외)
    resources/
      interface.json
      pipeline/, image/, model/, agent/ ...
    user/                   실행 후 생성되는 사용자 설정과 로그
```

이 저장소에는 MAABA/ANReroll의 게임 파이프라인·이미지·OCR 모델·개인 설정·세션 로그를 포함하지 않습니다. 리소스 배포자가 준비한 `assets/resources`를 별도로 설치합니다. `interface.json`의 상대 리소스 경로와 Agent 실행 경로 구조를 유지해야 합니다.

리소스 없이 실행하면 기본 이름 `MFWSide`의 Win32 설정 UI가 열리며 작업 시작은 비활성화됩니다. 상단 브랜드는 `interface.json.label`, 창 제목과 설정 내 앱 이름은 `interface.json.title`을 사용합니다. 각 값이 없으면 `MFWSide`를 사용합니다. 전체 PI 기능을 지원하는 것은 아니며 다중 리소스 선택, import/i18n 전체 해석 등은 별도 지원 범위입니다.

현재 의존성은 MaaFw 5.12.3 / PySide6 6.11.1 / opencv-python 4.13.0.92입니다. 리소스와 앱/MaaFramework 버전의 호환성을 맞춰 배포하세요.

## Agent

공식 `agent.child_exec` / `child_args` / `identifier` 선언으로 별도 Agent 프로세스를 실행합니다. `child_exec`와 스크립트 경로는 interface 위치를 기준으로 확인합니다. 새 Custom Action/Recognition 등록은 리소스의 Agent에서 담당하며 앱을 수정할 필요가 없습니다.

앱을 EXE로 빌드해도 리소스의 Python Agent 실행 환경까지 자동으로 제공되는 것은 아닙니다. Agent가 없는 리소스는 별도 Python 설치가 필요하지 않게 배포할 수 있지만, Python Agent가 있는 리소스는 별도 Python/가상환경/임베디드 런타임 또는 독립 Agent EXE와 의존성을 함께 준비해야 합니다. 앱 EXE를 Python 인터프리터로 사용하지 않습니다. `PI_CLIENT_NAME`은 리소스 제목과 별개의 클라이언트 식별자 `MFWSide`입니다.

## 검증과 배포

```powershell
.venv\Scripts\python.exe UnitTest/run_tests.py
```

기본 검사는 파일별 프로세스로 격리하며 실행·기기·전원 API를 모의합니다. 실제 게임/ADB 연결·로그인 등록·잠금/절전/종료를 수행하지 않습니다. native probe는 별도 수동 검증 도구입니다. `.cache/`의 진단 기록은 Git에 포함하지 않습니다.

- [빌드와 배포 안내](BUILDING.md)
- [MAABA에서 분리한 이력](HISTORY.md)

내부 저장 키와 드래그/아이콘/잠금·로그인 등록 식별자에 남아 있는 `MAABA` 접두사는 기존 설정 및 실행 프로세스와의 호환용입니다. 화면상의 앱 이름이나 대상 프로그램 탐색 조건으로 사용하지 않습니다.
