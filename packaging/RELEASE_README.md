# MFWSide Windows x64

MFWSide.exe와 _internal 폴더를 함께 유지하세요. 사용자 Python 설치가 필요 없는 폴더형 앱입니다.

이 배포물에는 게임 파이프라인·이미지·OCR 모델·개인 설정이 없습니다. 리소스 배포자가 제공한 assets/resources를 MFWSide.exe 옆에 별도로 설치하세요. interface.json이 없으면 기본 MFWSide UI가 열리고 작업 시작은 비활성화됩니다.

Python Agent를 사용하는 리소스는 해당 리소스가 지정한 외부 Python/가상환경 또는 Agent EXE를 별도로 구성해야 합니다. 앱 EXE는 Python Agent 인터프리터가 아닙니다. MaaAgentBinary는 ADB 캡처/입력에 필요한 디바이스 보조 바이너리이며 Python Agent 환경과 다릅니다.

assets/user는 처음 실행할 때 생성됩니다. 업데이트에서는 이 폴더를 보존하세요. EXE 옆에 쓰기 권한이 필요한 휴대용 구조이므로 쓰기 가능한 위치에 압축을 해제하세요.

BUILD-INFO.json에는 빌드 버전과 검증 결과, licenses에는 설치 패키지에 제공된 라이선스/고지를 포함합니다. 앱 자체 라이선스는 아직 별도로 선택하지 않았습니다. 공개 재배포 전 네이티브 의존성의 전체 고지/소스 제공 조건과 서명·실환경 검증을 확인해야 합니다. 이 빌드에는 코드 서명이 없습니다.
