"""Project Interface Agent processes owned by exactly one execution session."""
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from uuid import uuid4

from maa.agent_client import AgentClient
from maa.library import Library
from app.identity import DEFAULT_APP_NAME


AGENT_START_TIMEOUT_MS = 10_000
AGENT_REQUEST_TIMEOUT_MS = 30_000
AGENT_CLOSE_TIMEOUT_MS = 2_000
AGENT_EXIT_TIMEOUT_SECONDS = 3


@dataclass(frozen=True)
class AgentSpec:
    executable: str
    arguments: tuple[str, ...]
    identifier: str | None = None


def normalize_agents(value):
    if value is None:
        return []
    entries = [value] if isinstance(value, dict) else value
    if not isinstance(entries, list):
        raise ValueError("interface.json의 agent는 객체 또는 객체 배열이어야 합니다.")
    specs, identifiers = [], set()
    for index, item in enumerate(entries, 1):
        if not isinstance(item, dict):
            raise ValueError(f"Agent {index}: 설정은 객체여야 합니다.")
        executable = item.get("child_exec")
        arguments = item.get("child_args", [])
        identifier = item.get("identifier")
        if not isinstance(executable, str) or not executable.strip() or "\0" in executable:
            raise ValueError(f"Agent {index}: child_exec 실행 파일을 지정하세요.")
        if not isinstance(arguments, list) or any(not isinstance(arg, str) or "\0" in arg for arg in arguments):
            raise ValueError(f"Agent {index}: child_args는 문자열 배열이어야 합니다.")
        if identifier is not None and (not isinstance(identifier, str) or "\0" in identifier):
            raise ValueError(f"Agent {index}: identifier는 문자열이어야 합니다.")
        identifier = identifier or None
        if identifier:
            key = endpoint_key(identifier)
            if key in identifiers:
                raise ValueError(f"Agent {index}: identifier가 다른 Agent와 중복됩니다.")
            identifiers.add(key)
        specs.append(AgentSpec(executable, tuple(arguments), identifier))
    return specs


def endpoint_key(identifier):
    if identifier.isascii() and identifier.isdecimal() and 1 <= int(identifier) <= 65535:
        return "tcp:" + str(int(identifier))
    return "ipc:" + (identifier.casefold() if sys.platform == "win32" else identifier)


class EndpointLease:
    """Do not let this client's sessions reuse an explicitly fixed endpoint."""
    def __init__(self, identifier):
        directory = Path(tempfile.gettempdir()) / "maaba-agent-endpoints"
        directory.mkdir(parents=True, exist_ok=True)
        name = sha256(endpoint_key(identifier).encode("utf-8")).hexdigest() + ".lock"
        self.stream = (directory / name).open("a+b")
        if self.stream.seek(0, os.SEEK_END) == 0:
            self.stream.write(b"\0")
            self.stream.flush()
        self.stream.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self.stream.close()
            raise RuntimeError("Agent identifier가 다른 실행 세션에서 사용 중입니다. 병렬 실행 시 identifier를 생략하세요.") from error

    def close(self):
        # Closing releases the OS lock, including after a process crashes.
        self.stream.close()


def resolve_agent_executable(executable, directory):
    # A frozen client EXE is not a Python interpreter: never launch it as one.
    if executable.casefold() in ("python", "python.exe", "python3", "python3.exe") and not getattr(sys, "frozen", False):
        return str(Path(sys.executable).resolve())
    candidate = Path(executable)
    if not candidate.is_absolute():
        candidate = directory / candidate
    if candidate.is_file():
        resolved = str(candidate.resolve())
    else:
        resolved = shutil.which(executable)
    if not resolved:
        raise ValueError(f"Agent 실행 파일을 찾을 수 없습니다: {executable}. Python 환경 또는 별도 Agent 실행 파일을 준비하세요.")
    if getattr(sys, "frozen", False) and Path(resolved).resolve() == Path(sys.executable).resolve():
        raise ValueError("앱 실행 파일을 Agent 인터프리터로 사용할 수 없습니다.")
    if Path(resolved).suffix.casefold() in (".bat", ".cmd"):
        raise ValueError("Agent child_exec에는 배치 파일 대신 실행 파일을 지정하세요.")
    return resolved


def agent_environment(interface, controller, resource):
    environment = {key: value for key, value in os.environ.items() if not key.upper().startswith("PI_")}
    environment.update({
        "PI_INTERFACE_VERSION": "v2.5.0", "PI_CLIENT_NAME": DEFAULT_APP_NAME,
        "PI_CLIENT_VERSION": "", "PI_CLIENT_LANGUAGE": "ko_kr",
        "PI_CLIENT_MAAFW_VERSION": Library.version(),
        "PI_VERSION": str(interface.get("version", "")),
        "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8",
    })
    for name, selection in (("PI_CONTROLLER", controller), ("PI_RESOURCE", resource)):
        # This client has no full PI i18n resolver. Omit unavailable metadata,
        # as allowed by PI, rather than presenting translation keys as labels.
        if any(isinstance(selection.get(key), str) and selection[key].startswith("$")
               for key in ("label", "description", "icon")):
            continue
        environment[name] = json.dumps(selection, ensure_ascii=False, separators=(",", ":"))
    return environment


@dataclass
class OwnedAgent:
    client: object
    process: object = None
    output: object = None
    lease: object = None
    connected: bool = False
    log_path: Path | None = None


class AgentSession:
    def __init__(self, specs, interface_path, user_dir, interface, controller, resource, log=None):
        self.directory = Path(interface_path).resolve().parent
        self.commands = [(resolve_agent_executable(spec.executable, self.directory), spec) for spec in specs]
        self.log_dir = Path(user_dir) / "debug" / "agents" / uuid4().hex
        self.environment = agent_environment(deepcopy(interface), deepcopy(controller), deepcopy(resource))
        self.log = log or (lambda _message: None)
        self.agents = []
        self._bindings = None

    def start(self, resource, controller, tasker, cancellation_requested=None):
        if self.agents:
            raise RuntimeError("Agent 세션이 이미 시작되었습니다.")
        self._bindings = (resource, controller, tasker)
        actions, recognitions = set(), set()
        for index, (executable, spec) in enumerate(self.commands, 1):
            self._check_cancel(cancellation_requested)
            lease = EndpointLease(spec.identifier) if spec.identifier else None
            try:
                client = AgentClient(spec.identifier)
            except Exception:
                if lease is not None:
                    lease.close()
                raise
            entry = OwnedAgent(client, lease=lease)
            self.agents.append(entry)
            if not client.bind(resource):
                raise RuntimeError(f"Agent {index}: 리소스 바인딩에 실패했습니다.")
            identifier = client.identifier
            if not isinstance(identifier, str) or not identifier:
                raise RuntimeError(f"Agent {index}: 연결 식별자를 가져오지 못했습니다.")
            if not client.set_timeout(AGENT_START_TIMEOUT_MS):
                raise RuntimeError(f"Agent {index}: 연결 제한 시간을 설정하지 못했습니다.")
            self.log_dir.mkdir(parents=True, exist_ok=True)
            entry.log_path = self.log_dir / f"agent-{index}.log"
            entry.output = entry.log_path.open("ab", buffering=0)
            options = dict(cwd=str(self.directory), env=self.environment.copy(), shell=False,
                           stdin=subprocess.DEVNULL, stdout=entry.output, stderr=subprocess.STDOUT)
            if sys.platform == "win32":
                options["creationflags"] = subprocess.CREATE_NO_WINDOW
            entry.process = subprocess.Popen([executable, *spec.arguments, identifier], **options)
            self.log(f"Agent {index} 연결 중입니다. 진단 로그: {entry.log_path}")
            if entry.process.poll() is not None or not client.connect():
                raise RuntimeError(f"Agent {index}: 연결에 실패했습니다. 실행 환경/SDK 버전과 진단 로그를 확인하세요: {entry.log_path}")
            entry.connected = True
            self._check_cancel(cancellation_requested)
            if entry.process.poll() is not None:
                raise RuntimeError(f"Agent {index}: 연결 직후 프로세스가 종료되었습니다. 진단 로그: {entry.log_path}")
            new_actions, new_recognitions = set(client.custom_action_list), set(client.custom_recognition_list)
            if actions.intersection(new_actions) or recognitions.intersection(new_recognitions):
                raise RuntimeError(f"Agent {index}: 다른 Agent와 커스텀 이름이 중복됩니다.")
            actions.update(new_actions)
            recognitions.update(new_recognitions)
            if not client.set_timeout(AGENT_REQUEST_TIMEOUT_MS) or not client.register_sink(resource, controller, tasker):
                raise RuntimeError(f"Agent {index}: 이벤트 연결 또는 요청 제한 시간 설정에 실패했습니다.")
            self.log(f"Agent {index} 연결을 완료했습니다.")

    @staticmethod
    def _check_cancel(cancellation_requested):
        if cancellation_requested is not None and cancellation_requested():
            raise RuntimeError("Agent 시작이 취소되었습니다.")

    def ensure_running(self):
        for index, entry in enumerate(self.agents, 1):
            if entry.process is not None and entry.process.poll() is not None:
                raise RuntimeError(f"Agent {index} 프로세스가 종료되었습니다. 진단 로그: {entry.log_path}")

    @staticmethod
    def _reap(entry):
        process = entry.process
        if process is None:
            return
        if process.poll() is None:
            if entry.connected:
                try:
                    process.wait(timeout=AGENT_EXIT_TIMEOUT_SECONDS)
                    return
                except subprocess.TimeoutExpired:
                    pass
            process.terminate()
            try:
                process.wait(timeout=AGENT_CLOSE_TIMEOUT_MS / 1000)
                return
            except subprocess.TimeoutExpired:
                process.kill()
        process.wait(timeout=AGENT_CLOSE_TIMEOUT_MS / 1000)

    def close(self):
        errors, remaining = [], []
        for entry in reversed(self.agents):
            disconnected = entry.client is None
            if entry.client is not None:
                try:
                    if not entry.client.set_timeout(AGENT_CLOSE_TIMEOUT_MS) or not entry.client.disconnect():
                        raise RuntimeError("Agent 연결 해제에 실패했습니다.")
                    disconnected = True
                except Exception as error:
                    errors.append(str(error))
            try:
                self._reap(entry)
            except (OSError, subprocess.TimeoutExpired) as error:
                errors.append(f"Agent 프로세스 정리에 실패했습니다: {error}")
                remaining.append(entry)
                continue
            if not disconnected:
                remaining.append(entry)
                continue
            # SDK disconnect removes callbacks/sinks before their owners die.
            entry.process = None
            entry.client = None
            failed = False
            for name in ("output", "lease"):
                owned = getattr(entry, name)
                if owned is not None:
                    try:
                        owned.close()
                        setattr(entry, name, None)
                    except OSError as error:
                        errors.append(f"Agent {name} 정리에 실패했습니다: {error}")
                        failed = True
            if failed:
                remaining.append(entry)
        self.agents = list(reversed(remaining))
        if errors:
            raise RuntimeError(" ".join(dict.fromkeys(errors)))
        self._bindings = None
