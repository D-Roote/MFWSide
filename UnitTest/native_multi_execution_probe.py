"""Opt-in spawn/SDK lifecycle probe. No real window, game, ADB or power actions.

Run: .venv/Scripts/python.exe -X faulthandler UnitTest/native_multi_execution_probe.py
Only a synthetic CustomController is substituted inside test children; the
production session_process, Resource, Tasker, stop and cleanup paths are real.
"""
from copy import deepcopy
import multiprocessing as mp
import os
from pathlib import Path
from queue import Empty
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def probe_worker(snapshot, connection, mode, stop_event, events, desktop_lock):
    import numpy as np
    from maa.controller import Controller, CustomController
    from app.runtime import ControllerActivitySink
    from app.sessionRuntime import SessionRuntime
    import app.multiExecution as module

    class OwnedController(CustomController):
        def connect(self):
            return True

        def request_uuid(self):
            return f"multi-execution-probe-{os.getpid()}"

        def get_features(self):
            return 0

        def screencap(self):
            return np.zeros((180, 320, 3), dtype=np.uint8)

        def click(self, x, y):
            return True

    class ProbeRuntime(SessionRuntime):
        def _create_controller(self, *args, **kwargs):
            self.owner = OwnedController()
            self.controller = Controller(handle=self.owner._handle)
            self._controller_activity = ControllerActivitySink()
            self._controller_sink_id = self.controller.add_sink(self._controller_activity)
            events.put(("probe_pid", os.getpid()))
            return True, "Synthetic controller created"

        def release_session(self):
            result = super().release_session()
            if result[0] and hasattr(self, "owner"):
                self.owner = None
            return result

    module.SessionRuntime = ProbeRuntime
    module.session_process(snapshot, connection, mode, stop_event, events, desktop_lock)


def main():
    context = mp.get_context("spawn")
    bundle = Path(__file__).resolve().parent / "fixtures/native_monitor"
    with tempfile.TemporaryDirectory(prefix="maaba-multi-probe-") as directory:
        data = {"interface": {"controller": [{"name": "Probe", "type": "Adb"}], "task": []},
                "resource": {"path": [str(bundle)]}, "interface_path": str(bundle / "interface.json"),
                "user_dir": directory, "queue": [("Native_Run", {"Native_Wait": {"pre_delay": 3500}})],
                "minimize": False, "controller": {"name": "Probe"}, "program": {}, "settings": {}, "after_actions": {}}
        lock = context.Lock()
        for attempt in range(3):
            records = []
            for name in ("a", "b"):
                request = deepcopy(data)
                request["session_id"] = f"{attempt}-{name}"
                # A real existing-file marker satisfies preflight; the probe
                # overrides the factory with CustomController and never runs ADB.
                connection = {"preset": "Probe", "target": {"kind": "Adb", "key": name},
                              "preferences": {"adb_path": str(Path(__file__).resolve())}}
                stop, events = context.Event(), context.Queue(512)
                process = context.Process(target=probe_worker, args=(request, connection, "run", stop, events, lock))
                process.start()
                records.append({"process": process, "events": events, "stop": stop, "running": False})
            deadline = time.monotonic() + 25
            results, pids = {}, set()
            stopped = False
            while len(results) < 2 and time.monotonic() < deadline:
                for index, record in enumerate(records):
                    while True:
                        try:
                            kind, value = record["events"].get(timeout=0.01)
                        except Empty:
                            break
                        if kind == "probe_pid":
                            pids.add(value)
                        elif kind == "state" and value == "running":
                            record["running"] = True
                        elif kind == "result":
                            results[index] = value
                if all(record["running"] for record in records) and not stopped:
                    records[0]["stop"].set()
                    stopped = True
                time.sleep(0.01)
            if len(results) < 2:
                for record in records:
                    record["stop"].set()
            for record in records:
                record["process"].join(timeout=10)
                assert not record["process"].is_alive(), "Probe worker did not finish cleanup"
                assert record["process"].exitcode == 0, record["process"].exitcode
                record["process"].close()
                record["events"].close()
            assert len(pids) == 2 and os.getpid() not in pids, pids
            assert len(results) == 2, results
            assert not results[0]["succeeded"], results
            assert results[1]["succeeded"], results
            print(f"attempt {attempt + 1}: independent PIDs, A stopped, B completed, both clean exits")


if __name__ == "__main__":
    mp.freeze_support()
    main()
