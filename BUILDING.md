# Windows 앱 단독 빌드

## 권장: PyInstaller onedir

Python과 앱 의존성을 포함한 **폴더형(onedir) EXE**와 ZIP을 생성합니다. 사용자의 Python 설치나 실행 시 pip 설치에 의존하지 않습니다. 게임 리소스·개인 데이터는 번들 밖에 두며, 설치 프로그램이 아니라 쓰기 가능한 폴더에 압축을 푸는 휴대용 배포입니다.

공식 근거: [PyInstaller 사용법](https://pyinstaller.org/en/stable/usage.html), [런타임 경로](https://pyinstaller.org/en/stable/runtime-information.html), [PySide6 배포](https://doc.qt.io/qtforpython-6/deployment/deployment-pyinstaller.html).

## 준비와 빌드 명령

Windows x64 / CPython 3.10.11의 별도 가상환경에서 실행합니다. 앱 의존성과 PyInstaller 6.22.3·hooks 2026.8 및 전이 의존성은 requirements-build.txt에 고정합니다. 기존 가상환경을 복사하지 않습니다.

```powershell
py -3.10 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.venv\Scripts\python.exe -m pip check
.venv\Scripts\python.exe tools/build_windows.py
```

`packaging/MFWSide.spec`은 maa의 동적 DLL·메타데이터와 앱 UI/QSS/아이콘을 명시적으로 수집합니다. Android 캡처/입력용 MaaAgentBinary는 모든 아키텍처의 파일과 minicap .so까지 데이터로 보존합니다. 이는 리소스의 Python Agent 실행 환경과 다릅니다. QtUiTools/QtSvg와 플랫폼·SVG 플러그인, NumPy도 포함합니다. 앱에서 사용하지 않는 Python OpenCV 패키지(`opencv-python`, `cv2`)는 설치·번들 대상에서 제외하지만 MaaFW가 사용하는 별도 opencv_world4_maa.dll은 유지합니다. BUILD-INFO.json의 packages에는 빌드에 사용하는 패키지 버전을 기록합니다.

루트의 `VERSION`을 `1.0.0`과 같은 `주.부.수정` 형식으로 관리합니다. 빌드 정보, EXE의 파일·제품 버전과 ZIP 이름에 동일하게 적용되며, 배포 파일은 `dist/MFWSide-1.0.0-windows-x64.zip`과 `.zip.sha256`입니다. Windows 내부의 숫자 버전은 `1.0.0.0`으로 저장합니다. 릴리즈 태그는 `v1.0.0`, 제목은 `MFWSide 1.0.0` 형식을 사용합니다.

앱 단독 빌드는 `Start-ANReroll.cmd`를 생성하지 않으며 `License.Mareroll`·`License.MaaReroll`을 포함하지 않습니다. 라이선스 수집에서도 해당 리소스 전용 파일만 제외하고 Python·Qt·MaaFramework 등 앱 의존성의 라이선스는 보존합니다. 이 파일들이 다른 경로에서 번들에 유입되면 압축 전에 빌드를 중단합니다. 리소스를 별도로 배포할 때의 원저작자 고지 의무는 별도로 확인해야 합니다.

스크립트는 격리 회귀 → PyInstaller → 설치 패키지의 라이선스/고지 복사 → 실제 EXE 검증 → ZIP/SHA256 순서로 실행합니다. 회귀를 직전에 따로 통과했다면 --skip-tests, 콘솔 진단 빌드는 --console을 사용할 수 있습니다. 기존 출력에 assets가 있으면 덮어쓰지 않고 중단합니다.

실행 검증은 새 임시 복사본에서 Python/Qt/SDK 경로 환경변수를 제거하고 다른 작업 디렉토리에서 EXE를 시작합니다. 창 제목·최신 설정 저장·정상 창 닫기와 실제 EXE의 frozen multiprocessing 분기를 확인합니다. 별도 프로세스에서 번들의 SDK DLL과 버전을 확인하며 게임/ADB/전원/로그인 등록은 실행하지 않습니다. 성공한 검사 복사본은 제거하고 배포 폴더에 테스트 설정·로그를 남기지 않습니다. 이 검사는 Python을 물리적으로 제거한 별도 PC 검증을 대신하지 않습니다.

```text
배포 폴더/
  MFWSide.exe
  _internal/                Python, Qt, maa/bin 및 앱 UI 파일
  licenses/                 배포 의존성의 고지와 라이선스
  README.md                 리소스 설치와 외부 Agent 안내
  BUILD-INFO.json           소스 커밋·버전·검증 결과
  assets/                   앱 빌드 외부
    resources/              별도 설치하는 리소스 패키지
    user/                   실행 시 생성, 업데이트에서 보존
```

앱의 UI/아이콘은 번들 내부 `app` 기준으로, 외부 리소스/사용자 데이터는 EXE 옆 `assets` 기준으로 찾습니다. 별도 작업 디렉토리에서 실행해도 이 기준은 유지됩니다. 현재는 EXE 옆에 쓰기 권한이 필요한 휴대용 설치 구조이므로, Program Files처럼 쓰기 제한이 있는 위치에 설치하려면 사용자 데이터 경로 설계가 추가로 필요합니다.

## Agent와 리소스

- 앱 빌드만으로 임의의 Python Agent와 그 pip 의존성을 포함할 수는 없습니다. 리소스 작성자가 외부 Python/임베디드 환경 또는 Agent EXE를 함께 제공하고 공식 child_exec로 지정합니다.
- 게임별 모델/Paddle OCR 모델은 앱 내부에 넣지 않고 리소스 패키지의 `assets/resources/model` 등 해당 리소스 구조에 맞춰 배포합니다. 필요한 모델이 없으면 앱 빌드가 성공해도 OCR 작업은 실패할 수 있습니다.
- 앱/SDK 업데이트와 리소스 업데이트를 별도로 배포하더라도 검증된 호환 버전 조합을 명시합니다. 자동으로 모든 MaaFW 버전이나 리소스가 호환된다고 보장하지 않습니다.

## 배포 전 체크리스트

- [ ] Python이 설치되지 않은 별도 Windows 환경에서 실행, UI/아이콘/폰트·밝고 어두운 테마 확인
- [ ] interface 없음/유효한 Win32/유효한 ADB 리소스 각각 시작·경로·연결·캡처 확인
- [ ] 실제 기기 작업, 최소화/복구, 빠른 시작·중지 및 창 닫기 확인
- [ ] 다중 실행 spawn 자식이 새 GUI를 열지 않고 독립 세션을 수행하는지 확인
- [ ] Agent 없는 리소스, 외부 Python Agent, 독립 Agent EXE를 별도로 확인
- [ ] 작업 완료 후 앱 종료와 잠금·절전·최대 절전 도우미 확인 (실제 전원 동작은 사용자 검증 필요)
- [ ] 앱/리소스 업데이트가 `assets/user`를 덮어쓰지 않는지 확인
- [ ] 배포물에 개발 설정·로그·계정·대형 세션 이미지가 들어 있지 않은지 확인
- [ ] 의존 라이브러리 라이선스/고지/필요한 소스 제공 의무와 원래 리소스의 배포 권한 검토

MaaFramework 5.12.3의 [라이선스 원문](https://github.com/MaaXYZ/MaaFramework/blob/v5.12.3/LICENSE.md), Qt/PySide와 각 DLL·모델의 배포 조건을 함께 확인하세요. 앱 자체의 라이선스는 별도로 선택해야 하며, 저장소를 분리했다고 새 라이선스를 임의 부여하지 않습니다.

## 대안과 제한

`onefile`은 이번 빌드에 포함하지 않습니다. 실행 때 임시 추출이 필요하고 네이티브 DLL/다중 실행/종료 후 독립 프로세스 처리가 더 복잡합니다. 종료 도우미는 headless 모드와 독립 실행 환경 처리를 갖추었지만 실제 잠금·절전·종료는 수행하지 않습니다. [독립 자식 프로세스 주의사항](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html#using-sys-executable-to-spawn-subprocesses-that-outlive-the-application-process-implementing-application-restart)을 참고하세요.

Nuitka/pyside6-deploy도 대안이지만 이번 결과물은 PyInstaller 기준입니다. 실제 기기/게임·Python Agent 조합·완료 전원 동작·서명·공개 재배포 조건은 배포 전 체크리스트에 따라 별도로 검증합니다. 앱 자체 라이선스는 임의로 부여하지 않으며 설치 패키지의 고지만 복사했다고 모든 재배포 검토가 끝난 것은 아닙니다.

## 2026-10-20 실제 빌드 결과

- 앱 변경: MAABA `3ca9f38` → MFWSide `f23b9fc`. 빌드 소스 커밋은 `b8c1591`이며 이 이후 결과 기록은 문서만 변경한다.
- 환경: CPython 3.10.11 x64, MaaFw 5.12.3, PySide6 6.11.1, PyInstaller 6.22.3, hooks 2026.8.
- 실행 파일: `dist/MFWSide/MFWSide.exe`. 폴더 전체 341개 파일, 227,187,328바이트(약 216.7MiB). EXE만 따로 옮기지 않는다.
- 압축 배포: `dist/MFWSide-windows-x64-20261020.zip`, 88,858,696바이트(약 84.7MiB). 같은 위치의 `.zip.sha256`을 함께 제공한다. Git에는 생성물을 넣지 않는다.
- ZIP SHA256: `a1730e3f5b8e2decca2d3ecf5df38bd865413794a6f1a9b55243724894b00cc5`.
- EXE SHA256: `e51956892a824c1b0fc09e85486a08faef0de1a5c871ccacba267c7c039ad552`.
- 새 가상환경의 최종 전체 격리 회귀 52개 모듈·809개 검사 실패 0건, Windows symlink 권한 제한 skip 1건. 로그는 `.cache/post-build-regression-20261020.log`.
- 실제 번들 검사: 필수 파일 17개, Android 디바이스 보조 파일/.so, SDK DLL 로딩과 v5.12.3, MFWSide 기본 UI와 최신 설정 저장, 정상 창 닫기, 실제 EXE의 frozen multiprocessing 통신·정상 종료 모두 통과했다. Python이 없는 PATH·다른 작업 디렉토리·개인 설정 없는 임시 복사본에서 검사했다.
- ZIP 341개 항목의 CRC 정상, 단일 MFWSide 루트, 외부 assets/개인 설정/테스트/.venv/.cache/.git 없음. 배포 폴더에도 테스트로 생성한 assets가 남지 않는다. 설치 패키지의 라이선스·고지와 빌드/검증 JSON은 포함한다.
- PyInstaller 경고 목록은 운영체제 분기·선택적 모듈·NumPy 확장 심볼 관련 항목을 확인했다. 실제 Windows 앱 import·DLL 로딩·spawn 검증이 통과했다는 범위이며 모든 선택적 패키지를 설치하지는 않는다.
- 미검증: Python 미설치 별도 PC/VM, 실제 Win32·ADB 자동화, 프로젝트별 Python Agent, 잠금/절전/최대 절전/시스템 종료. EXE는 미서명이며 공개 배포 전 라이선스·고지/소스 제공 검토도 별도로 필요하다.
