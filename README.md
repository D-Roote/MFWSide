# MFWSide

MaaFramework와 PySide6 기반의 Windows용 MAA Framework 범용 실행 앱입니다. 게임별 파이프라인과 리소스를 앱과 분리하여 구성합니다.

---

## 주요 기능

- 리소스의 `interface.json`에 정의된 작업·세부 옵션 구성과 프리셋 저장
- Win32·ADB 연결 확인, 화면 캡처·연속 모니터링과 실행 로그 확인
- 여러 인스턴스의 개별·일괄 실행과 인스턴스별 프리셋 선택

---

## 사용 방법

1. Windows x64용 배포 폴더 전체를 쓰기 가능한 경로에 압축 해제합니다. `MFWSide.exe`와 `_internal/`은 함께 유지합니다.
2. `interface.json`을 `MFWSide.exe` 옆에 설치합니다. 리소스와 Agent는 `interface.json`에 선언한 경로에 둡니다. 일반적인 구조는 `resource/`, `agent/`, `user/`, `interface.json`, `MFWSide.exe`입니다.
3. `MFWSide.exe`를 실행하고 연결 대상과 작업을 선택한 뒤 시작합니다.

배포 앱에는 Python이 포함되지만 게임별 리소스는 포함되지 않습니다. **Python Agent를 사용하는 리소스는 별도 Python 실행 환경과 해당 Agent의 의존성이 필요합니다.**

Agent 연결은 `127.0.0.1`의 로컬 TCP를 사용하며 기본 포트는 자동 할당합니다. `agent.identifier`에 `1~65535`의 숫자 문자열을 지정하면 해당 포트를 사용합니다. 기존 문자열 식별자는 중복 실행 방지용 이름으로 유지하고, Agent 실행 인수에는 실제 할당된 포트번호를 전달합니다. Agent는 전달받은 식별자를 `AgentServer.start_up()`에 사용해야 합니다.

사용자 설정과 세션 로그는 EXE 옆의 `user/`에 저장됩니다. 앱이나 리소스를 교체할 때 이 폴더를 유지해야 사용자 설정이 유지됩니다.

기존 `assets` 구조에서 전환할 때는 앱을 종료한 상태에서 `assets/user/`를 최상위 `user/`로 옮기고, `interface.json`의 위치가 바뀐 만큼 `resource[].path`와 Agent 실행 경로를 조정하세요. 예를 들어 기존 리소스의 `path: ["."]`는 `resource/`로 옮겼다면 `["resource"]`로 바꿉니다. 앱이 기존 파일을 자동 이동하거나 새·이전 설정을 병합하지는 않습니다.

`interface.json`이 없으면 기본 이름 `MFWSide`로 열리며 작업 실행은 비활성화됩니다.  
`interface.json`의 모든 기능을 지원하는 것은 아니므로 앱·MaaFramework와 리소스의 호환성을 확인하세요.

---

## 개발 환경

Windows x64와 Python 3.10을 기준으로 개발합니다. 저장소 루트에서 새 가상환경을 만들어 실행합니다.

```powershell
py -3.10 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe main.py
```

앱 의존성 버전은 [의존성 목록](requirements.txt)을 기준으로 합니다. 테스트는 다음 명령으로 실행합니다.

```powershell
.venv\Scripts\python.exe UnitTest/run_tests.py
```

폴더형 실행 파일과 압축 배포물 생성 방법, 검증 범위는 [빌드·배포 안내](BUILDING.md)를 참고하세요.

---

## 참고

참고한 프로젝트는 다음과 같습니다.

- [MFAAvalonia](https://github.com/MaaXYZ/MFAAvalonia)
- [MFW-PyQt6](https://github.com/overflow65537/MFW-PyQt6)
