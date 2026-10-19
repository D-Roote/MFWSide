# Windows 앱 단독 빌드

## 권장: PyInstaller onedir

첫 배포는 Python과 앱 의존성을 포함한 **폴더형(onedir) EXE**를 권장합니다. 사용자의 Python 설치나 실행 시 pip 설치에 의존하지 않고, DLL 누락·Qt 플러그인·다중 실행을 진단하기 쉽습니다. ZIP 또는 설치 프로그램으로 폴더 전체를 배포합니다. 실제 빌드는 아직 수행하지 않았으며 다음은 검증을 시작하기 위한 빌드안입니다.

공식 근거: [PyInstaller 사용법](https://pyinstaller.org/en/stable/usage.html), [런타임 경로](https://pyinstaller.org/en/stable/runtime-information.html), [PySide6 배포](https://doc.qt.io/qtforpython-6/deployment/deployment-pyinstaller.html).

## 준비와 빌드 명령

Windows x64의 별도 빌드 가상환경에서 실행합니다. 개발 환경과 같은 Python 3.10을 시작 기준으로 사용하고 빌드 도구 버전도 릴리즈 시 고정하세요.

```powershell
py -3.10 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m pip install pyinstaller
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onedir --windowed --name MFWSide --collect-all maa --add-data "app/pySide6:app/pySide6" --add-data "app/resources:app/resources" main.py
```

`maa`는 DLL을 동적으로 로드합니다. Python 모듈만 포함하면 부족하므로 `maa/bin`의 MaaFramework/Toolkit/AgentClient/ControlUnit 및 OpenCV/ONNX/DirectML 등 의존 DLL도 함께 수집해야 합니다. `--collect-all maa`는 시작점이며 빌드 결과에서 실제 파일을 확인해야 합니다. QtUiTools/QtSvg, Qt 플랫폼·이미지 플러그인과 Python cv2/NumPy도 함께 확인합니다.

첫 디버그 빌드에서는 `--windowed`를 빼서 콘솔을 보이게 하고, 정상 실행을 확인한 뒤 GUI 배포 옵션을 사용하세요. 위 명령은 `assets`, 테스트, 사용자 로그·설정이나 `.venv` 전체를 데이터로 넣지 않습니다.

```text
배포 폴더/
  MFWSide.exe
  _internal/                Python, Qt, maa/bin 및 앱 UI 파일
  licenses/                 배포 의존성의 고지와 라이선스
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

`onefile`은 초기 검증 이후 고려합니다. 실행 때 임시 추출이 필요하고 네이티브 DLL/다중 실행/종료 후 독립 프로세스 처리가 더 복잡합니다. 종료 도우미는 별도 headless 모드와 PyInstaller 독립 실행 환경 처리를 갖추었지만 실제 EXE 빌드를 검증했다는 뜻은 아닙니다. [독립 자식 프로세스 주의사항](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html#using-sys-executable-to-spawn-subprocesses-that-outlive-the-application-process-implementing-application-restart)을 참고하세요.

Nuitka/pyside6-deploy도 대안이지만 현재 코드는 PyInstaller의 frozen 경로 및 독립 자식 실행 규약을 기준으로 점검했습니다. 다른 빌더는 DLL·데이터 포함과 frozen 진입점 판별을 별도로 검증해야 합니다. 이 단계에서는 빌드 완료나 모든 배포 기능의 실기기 검증을 보장하지 않습니다.
