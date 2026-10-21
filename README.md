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
2. 별도 리소스 패키지를 `assets/resources/`에 설치합니다. `interface.json`과 파이프라인·이미지·모델·Agent의 상대 경로를 유지합니다.
3. `MFWSide.exe`를 실행하고 연결 대상과 작업을 선택한 뒤 시작합니다.

배포 앱에는 Python이 포함되지만 게임별 리소스는 포함되지 않습니다. **Python Agent를 사용하는 리소스는 별도 Python 실행 환경과 해당 Agent의 의존성이 필요합니다.**

사용자 설정과 세션 로그는 `assets/user/`에 저장됩니다. 앱이나 리소스를 교체할 때 이 폴더를 유지해야 사용자 설정이 유지됩니다.

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
