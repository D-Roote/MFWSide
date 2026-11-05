"""Per-session IPC: blocking pipe reads never run on the Qt/GUI thread."""
from collections import deque
from queue import Empty
import threading
import time


class SessionEventSender:
    def __init__(self, connection):
        self.connection = connection
        self._condition = threading.Condition()
        self._pending = deque(maxlen=512)
        self._critical = {}
        self._sending = False
        self._closing = False
        self._error = None
        self._thread = None

    def __getstate__(self):
        return {"connection": self.connection}

    def __setstate__(self, state):
        self.__init__(state["connection"])

    def put(self, value, timeout=0):
        # SDK callbacks only enqueue. Connection.send has no timeout and must
        # never hold a callback/task thread waiting for a stalled GUI.
        with self._condition:
            if self._error is not None or self._closing:
                raise OSError("세션 메시지 송신이 종료되었습니다.") from self._error
            if value[0] in ("result", "disconnected"):
                self._critical[value[0]] = value
            else:
                self._pending.append(value)
            if self._thread is None:
                self._thread = threading.Thread(target=self._send, name="session-send", daemon=True)
                try:
                    self._thread.start()
                except RuntimeError as error:
                    self._error = error
                    self._thread = None
                    raise OSError("세션 송신 스레드를 시작하지 못했습니다.") from error
            self._condition.notify_all()

    def _send(self):
        try:
            while True:
                with self._condition:
                    while not self._critical and not self._pending:
                        if self._closing:
                            return
                        self._condition.wait()
                    if self._critical:
                        key = next(iter(self._critical))
                        item = self._critical.pop(key)
                    else:
                        item = self._pending.popleft()
                    self._sending = True
                self.connection.send(item)
                with self._condition:
                    self._sending = False
                    self._condition.notify_all()
        except Exception as error:
            with self._condition:
                self._error = error
                self._sending = False
                self._condition.notify_all()
        finally:
            self.connection.close()

    def flush(self, timeout=2):
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._pending or self._critical or self._sending:
                if self._error is not None:
                    raise OSError("세션 메시지를 전송하지 못했습니다.") from self._error
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("세션 메시지 전송 대기 시간이 초과되었습니다.")
                self._condition.wait(remaining)
            if self._error is not None:
                raise OSError("세션 메시지를 전송하지 못했습니다.") from self._error

    def close(self):
        with self._condition:
            self._closing = True
            if self._thread is None:
                self.connection.close()  # Parent's unused writer copy.
            self._condition.notify_all()
        # The sender owns its active handle. Never close it from a second
        # thread during native I/O, or join it on the GUI thread.


class SessionEventInbox:
    def __init__(self, context):
        self._reader, writer = context.Pipe(duplex=False)
        self.sender = SessionEventSender(writer)
        self._pending = deque(maxlen=512)
        self._result = None
        self._disconnected = None
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._closed = threading.Event()
        self.error = ""
        self._thread = None

    @property
    def reader_done(self):
        return self._done.is_set()

    def start(self):
        self._thread = threading.Thread(target=self._receive, name="session-events", daemon=True)
        self._thread.start()

    def close_sender(self):
        # Only the spawned child keeps a writer. Thus its exit also produces
        # EOF during a partial frame instead of leaving the GUI waiting.
        self.sender.close()

    def _receive(self):
        try:
            while not self._closed.is_set():
                item = self._reader.recv()
                if not isinstance(item, tuple) or len(item) != 2:
                    raise ValueError("올바르지 않은 세션 메시지")
                with self._lock:
                    if item[0] == "result":
                        self._result = item  # Never evict the terminal result.
                    elif item[0] == "disconnected":
                        self._disconnected = item
                    else:
                        self._pending.append(item)
        except EOFError:
            pass
        except Exception as error:
            self.error = f"세션 통신이 끊어졌습니다: {error}"
        finally:
            self._reader.close()
            self._done.set()

    def get_nowait(self):
        with self._lock:
            if self._disconnected is not None:
                item, self._disconnected = self._disconnected, None
                return item
            if self._pending:
                return self._pending.popleft()
            if self._result is not None:
                item, self._result = self._result, None
                return item
        raise Empty

    def close(self):
        self._closed.set()
        self.sender.close()
        if self._thread is None or self._thread.ident is None:
            self._reader.close()
            self._done.set()
        # The receive thread owns/closes its endpoint. Never join a blocked
        # reader or close a Windows handle concurrently with its native read.


class SharedFlag:
    """One parent writer, byte-sized readers; no crash-abandoned OS mutex."""
    def __init__(self, context):
        self._value = context.RawValue("b", 0)

    def is_set(self):
        return bool(self._value.value)

    def set(self):
        self._value.value = 1

    def wait(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        while not self.is_set():
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                time.sleep(min(0.01, remaining))
            else:
                time.sleep(0.01)
        return True


class DesktopLease:
    """Parent-owned Win32 input admission; child death cannot strand a mutex."""
    def __init__(self, ready):
        self.ready = ready

    def acquire(self, timeout=None):
        return self.ready.wait(timeout)

    def release(self):
        pass  # The parent grants the next lease only after this child exits.
