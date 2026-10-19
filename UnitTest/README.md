# 앱 회귀 검사

```powershell
.venv\Scripts\python.exe UnitTest/run_tests.py
.venv\Scripts\python.exe UnitTest/run_tests.py -p test_application_title.py
```

Windows에서 requirements.txt의 앱 의존성을 설치한 가상환경을 사용합니다. 테스트 실행기는 모듈별로 프로세스를 분리하고, `.cache/test_diagnostics`에 PID별 Python 네이티브 오류 기록을 남깁니다. Windows 오류창에서 무한 대기하지 않도록 테스트 프로세스에만 오류 모드를 적용하며, 실패·비정상 종료·시간 초과를 숨기거나 자동 재시도하지 않습니다.

기본 검사는 Qt offscreen과 모의 실행/컨트롤러/ADB/Windows API, 임시 파일을 사용합니다. 실제 게임·기기·프로세스 실행/종료, 잠금/절전/시스템 종료, 로그인 등록, 개인 로그 삭제를 하지 않습니다. 사용자 interface가 없어도 실행할 수 있도록 런타임 선언은 테스트 fixture로 제공합니다. fixture 안의 프로그램/작업 이름은 모의 문자열이며 실제 게임 리소스가 필요하지 않습니다.

앱 경로·Agent 실행·자동 실행·연결/캡처·다중 실행·작업 설정·프리셋·관리·UI 배치·밝고 어두운 테마·종료 후 동작을 검증합니다. `test_frozen_helper.py`는 종료 후 도우미가 GUI/SDK 초기화를 거치지 않는지 확인합니다. frozen 관련 검사는 실제 EXE 빌드나 실기기 검증을 대체하지 않습니다.

MAABA 게임 리소스를 실제로 읽던 `test_pipeline_defaults.py`는 앱 전용 이력에서 제외했습니다. `UnitTest/fixtures/native_monitor`의 작은 합성 파이프라인은 테스트 데이터입니다. `native_*_probe.py`는 별도 수동 SDK 진단용이며 기본 테스트에서 실행하지 않습니다.

`--in-process --collect-between-tests`는 과거 Qt/Shiboken 정리 충돌을 재현하는 진단 옵션입니다. 일반 회귀는 기본 파일별 격리 실행을 사용하세요. Windows symlink 생성 권한이 없으면 해당 실제 링크 검사만 skip할 수 있습니다.
