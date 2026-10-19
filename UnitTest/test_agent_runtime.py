"""PI Agent lifecycle using fake SDK clients/processes; never start user agents."""
from contextlib import ExitStack
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agentRuntime import (
    AGENT_CLOSE_TIMEOUT_MS, AGENT_REQUEST_TIMEOUT_MS, AGENT_START_TIMEOUT_MS,
    AgentSession, AgentSpec, EndpointLease, agent_environment, endpoint_key,
    normalize_agents, resolve_agent_executable,
)
from app.runtime import AppRuntime, PROGRAM_LAUNCH_ENTRY
from app.sessionRuntime import SessionRuntime


def interface_config():
    return {
        "interface_version": 2, "version": "resource-1.0",
        "controller": [{"name": "Win", "type": "Win32", "win32": {"window_regex": "Game"}},
                       {"name": "Android", "type": "Adb"}],
        "resource": [{"name": "Default", "path": ["."]}],
        "task": [{"name": "Task", "entry": "Task"}],
        "agent": {"child_exec": "python", "child_args": ["agent/main.py"]},
    }


def client_mock(identifier="generated-id"):
    client = MagicMock(identifier=identifier, custom_action_list=[], custom_recognition_list=[])
    for name in ("bind", "connect", "disconnect", "set_timeout", "register_sink"):
        getattr(client, name).return_value = True
    return client


def process_mock():
    process = MagicMock()
    process.poll.return_value = None
    process.wait.return_value = 0
    return process


class AgentConfigurationTests(unittest.TestCase):
    def test_missing_empty_single_and_multiple_agents(self):
        self.assertEqual(normalize_agents(None), [])
        self.assertEqual(normalize_agents([]), [])
        self.assertEqual(normalize_agents({"child_exec": "python"}), [AgentSpec("python", ())])
        self.assertEqual(normalize_agents([{"child_exec": "one"}, {"child_exec": "two", "identifier": ""}]),
                         [AgentSpec("one", ()), AgentSpec("two", ())])

    def test_invalid_configuration_is_rejected_without_starting_native_code(self):
        bad = ("agent", 1, [None], {}, {"child_exec": "  "}, {"child_exec": "a\0b"},
               {"child_exec": "python", "child_args": "main.py"},
               {"child_exec": "python", "child_args": [1]},
               {"child_exec": "python", "child_args": ["a\0b"]},
               {"child_exec": "python", "identifier": 55},
               {"child_exec": "python", "identifier": "a\0b"})
        with patch("app.agentRuntime.AgentClient") as native, patch("app.agentRuntime.subprocess.Popen") as launch:
            for value in bad:
                with self.subTest(value=value), self.assertRaises(ValueError):
                    normalize_agents(value)
        native.assert_not_called()
        launch.assert_not_called()

    def test_duplicate_identifiers_include_equivalent_tcp_ports(self):
        for identifiers in (("same", "same"), ("05555", "5555")):
            with self.subTest(identifiers=identifiers), self.assertRaisesRegex(ValueError, "중복"):
                normalize_agents([{"child_exec": "agent", "identifier": name} for name in identifiers])
        self.assertEqual(endpoint_key("65535"), "tcp:65535")
        self.assertTrue(endpoint_key("65536").startswith("ipc:"))

    def test_source_python_uses_current_virtual_environment(self):
        with patch.object(sys, "frozen", False, create=True), patch("app.agentRuntime.shutil.which") as which:
            for executable in ("python", "PYTHON.EXE", "python3"):
                self.assertEqual(resolve_agent_executable(executable, Path.cwd()), str(Path(sys.executable).resolve()))
        which.assert_not_called()

    def test_relative_executable_is_relative_to_interface_not_current_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            executable = directory / "agent.exe"
            executable.touch()
            self.assertEqual(resolve_agent_executable("agent.exe", directory), str(executable.resolve()))

    def test_missing_executable_and_batch_files_are_rejected(self):
        with patch("app.agentRuntime.shutil.which", return_value=None), self.assertRaisesRegex(ValueError, "찾을 수"):
            resolve_agent_executable("missing-agent", Path.cwd())
        with patch("app.agentRuntime.shutil.which", return_value="agent.cmd"), self.assertRaisesRegex(ValueError, "배치"):
            resolve_agent_executable("agent.cmd", Path.cwd())

    def test_frozen_requires_external_interpreter_and_never_recurses_into_main_exe(self):
        with patch.object(sys, "frozen", True, create=True):
            with patch("app.agentRuntime.shutil.which", return_value="external/python.exe"):
                self.assertEqual(resolve_agent_executable("python", Path.cwd()), "external/python.exe")
            with patch("app.agentRuntime.shutil.which", return_value=sys.executable), self.assertRaisesRegex(ValueError, "인터프리터"):
                resolve_agent_executable("python", Path.cwd())

    def test_environment_uses_selected_controller_resource_and_resource_version(self):
        interface = interface_config()
        controller, resource = interface["controller"][1], interface["resource"][0]
        with patch.dict(os.environ, {"PI_CONTROLLER": "old", "pi_resource": "old", "CUSTOM_ENV": "keep"}), \
                patch("app.agentRuntime.Library.version", return_value="5.12.3"):
            environment = agent_environment(interface, controller, resource)
            self.assertEqual(environment["PI_CLIENT_NAME"], "MFWSide")
        self.assertEqual(json.loads(environment["PI_CONTROLLER"]), controller)
        self.assertEqual(json.loads(environment["PI_RESOURCE"]), resource)
        self.assertNotIn("\n", environment["PI_CONTROLLER"])
        self.assertNotIn("pi_resource", environment)
        self.assertEqual(environment["PI_CLIENT_MAAFW_VERSION"], "5.12.3")
        self.assertEqual(environment["PI_VERSION"], "resource-1.0")
        self.assertEqual(environment["PI_CLIENT_VERSION"], "")
        self.assertEqual(environment["CUSTOM_ENV"], "keep")

    def test_unresolved_i18n_metadata_is_omitted_not_passed_as_translated(self):
        with patch.dict(os.environ, {"PI_CONTROLLER": "stale", "PI_RESOURCE": "stale"}), \
                patch("app.agentRuntime.Library.version", return_value="5.12.3"):
            environment = agent_environment({}, {"label": "$controller"}, {"description": "$resource"})
        self.assertNotIn("PI_CONTROLLER", environment)
        self.assertNotIn("PI_RESOURCE", environment)

    def test_explicit_endpoint_is_exclusively_owned_until_closed(self):
        with tempfile.TemporaryDirectory() as temporary, patch("app.agentRuntime.tempfile.gettempdir", return_value=temporary):
            first = EndpointLease("5555")
            try:
                with self.assertRaisesRegex(RuntimeError, "다른 실행 세션"):
                    EndpointLease("05555")
            finally:
                first.close()
            following = EndpointLease("5555")
            following.close()
            following.close()


class AgentTestBase(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.directory = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.clients, self.processes, self.managers = [], [], []
        self.native = self.stack.enter_context(patch("app.agentRuntime.AgentClient", side_effect=self.make_client))
        self.launch = self.stack.enter_context(patch("app.agentRuntime.subprocess.Popen", side_effect=self.make_process))
        self.lease = self.stack.enter_context(patch("app.agentRuntime.EndpointLease"))
        self.stack.enter_context(patch("app.agentRuntime.Library.version", return_value="5.12.3"))
        self.addCleanup(self.close_managers)

    def make_client(self, identifier=None):
        client = client_mock(identifier or f"generated-{len(self.clients)}")
        self.clients.append(client)
        return client

    def make_process(self, *args, **kwargs):
        process = process_mock()
        self.processes.append(process)
        return process

    def close_managers(self):
        for manager in self.managers:
            # Even failed assertions must close file handles without native work.
            for entry in manager.agents:
                if entry.client is not None:
                    entry.client.set_timeout.side_effect = None
                    entry.client.set_timeout.return_value = True
                    entry.client.disconnect.side_effect = None
                    entry.client.disconnect.return_value = True
                if entry.process is not None:
                    entry.process.poll.side_effect = None
                    entry.process.poll.return_value = 0
                    entry.process.wait.side_effect = None
                for owned in (entry.output, entry.lease):
                    if isinstance(owned, MagicMock):
                        owned.close.side_effect = None
            manager.close()

    def manager(self, value=None):
        interface = interface_config()
        manager = AgentSession(normalize_agents(value or interface["agent"]), self.directory / "interface.json",
                               self.directory / "user", interface, interface["controller"][0], interface["resource"][0])
        self.managers.append(manager)
        return manager


class AgentSessionTests(AgentTestBase):
    def test_start_binds_before_connect_registers_sinks_and_appends_actual_identifier(self):
        manager = self.manager()
        bindings = (object(), object(), object())
        manager.start(*bindings)
        client = self.clients[0]
        self.assertEqual(client.mock_calls, [call.bind(bindings[0]), call.set_timeout(AGENT_START_TIMEOUT_MS),
                         call.connect(), call.set_timeout(AGENT_REQUEST_TIMEOUT_MS), call.register_sink(*bindings)])
        args, options = self.launch.call_args
        self.assertEqual(args[0], [str(Path(sys.executable).resolve()), "agent/main.py", client.identifier])
        self.assertEqual(options["cwd"], str(self.directory.resolve()))
        self.assertFalse(options["shell"])
        self.assertEqual(options["stdin"], subprocess.DEVNULL)
        self.assertEqual(options["stderr"], subprocess.STDOUT)
        self.assertEqual(json.loads(options["env"]["PI_CONTROLLER"])["name"], "Win")
        self.assertEqual(manager._bindings, bindings)
        self.assertTrue(manager.agents[0].log_path.is_file())

    def test_multiple_agents_use_independent_clients_and_reverse_cleanup_order(self):
        manager = self.manager([{"child_exec": "python"}, {"child_exec": "python", "identifier": "fixed"}])
        manager.start(object(), object(), object())
        self.assertEqual(self.native.call_args_list, [call(None), call("fixed")])
        self.lease.assert_called_once_with("fixed")
        events = []
        for index, client in enumerate(self.clients):
            client.disconnect.side_effect = lambda index=index: events.append(index) or True
        manager.close()
        self.assertEqual(events, [1, 0])
        self.assertEqual(manager.agents, [])
        self.assertIsNone(manager._bindings)
        for process in self.processes:
            process.wait.assert_called_once_with(timeout=3)
            process.terminate.assert_not_called()
        manager.close()

    def test_cancellation_before_start_creates_no_native_client_or_process(self):
        manager = self.manager()
        with self.assertRaisesRegex(RuntimeError, "취소"):
            manager.start(object(), object(), object(), lambda: True)
        self.native.assert_not_called()
        self.launch.assert_not_called()
        manager.close()

    def test_cancellation_after_handshake_still_cleans_connected_agent(self):
        manager = self.manager()
        cancellation = MagicMock(side_effect=[False, True])
        with self.assertRaisesRegex(RuntimeError, "취소"):
            manager.start(object(), object(), object(), cancellation)
        self.assertTrue(manager.agents[0].connected)
        manager.close()
        self.clients[0].disconnect.assert_called_once()
        self.clients[0].register_sink.assert_not_called()

    def test_bind_failure_never_launches_process(self):
        client = self.make_client()
        client.bind.return_value = False
        self.native.side_effect = None
        self.native.return_value = client
        manager = self.manager()
        with self.assertRaisesRegex(RuntimeError, "바인딩"):
            manager.start(object(), object(), object())
        self.launch.assert_not_called()
        manager.close()
        client.disconnect.assert_called_once()

    def test_native_creation_failure_releases_fixed_endpoint(self):
        manager = self.manager({"child_exec": "python", "identifier": "fixed"})
        self.native.side_effect = RuntimeError("create failed")
        with self.assertRaisesRegex(RuntimeError, "create failed"):
            manager.start(object(), object(), object())
        self.lease.return_value.close.assert_called_once()
        self.launch.assert_not_called()
        manager.close()

    def test_endpoint_conflict_never_constructs_native_client(self):
        manager = self.manager({"child_exec": "python", "identifier": "fixed"})
        self.lease.side_effect = RuntimeError("in use")
        with self.assertRaisesRegex(RuntimeError, "in use"):
            manager.start(object(), object(), object())
        self.native.assert_not_called()
        self.launch.assert_not_called()

    def test_invalid_sdk_identifier_and_timeout_failure_do_not_launch_child(self):
        for invalid_identifier, invalid_timeout in ((True, False), (False, True)):
            with self.subTest(identifier=invalid_identifier, timeout=invalid_timeout):
                client = self.make_client()
                if invalid_identifier:
                    client.identifier = None
                if invalid_timeout:
                    client.set_timeout.return_value = False
                self.native.side_effect = None
                self.native.return_value = client
                manager = self.manager()
                with self.assertRaises(RuntimeError):
                    manager.start(object(), object(), object())
                self.launch.assert_not_called()
                client.set_timeout.return_value = True
                manager.close()

    def test_child_exit_immediately_after_handshake_blocks_sink_registration(self):
        process = self.make_process()
        process.poll.side_effect = [None, 1]
        self.launch.side_effect = None
        self.launch.return_value = process
        manager = self.manager()
        with self.assertRaisesRegex(RuntimeError, "연결 직후"):
            manager.start(object(), object(), object())
        self.clients[0].register_sink.assert_not_called()
        process.poll.side_effect = None
        process.poll.return_value = 1
        manager.close()

    def test_arguments_remain_separate_without_shell_interpolation(self):
        manager = self.manager({"child_exec": "python", "child_args": ["agent/main.py", "--name", "공백 포함", "&literal"]})
        manager.start(object(), object(), object())
        self.assertEqual(self.launch.call_args.args[0][1:], ["agent/main.py", "--name", "공백 포함", "&literal", self.clients[0].identifier])
        self.assertFalse(self.launch.call_args.kwargs["shell"])

    def test_an_already_started_session_cannot_launch_a_second_set(self):
        manager = self.manager()
        manager.start(object(), object(), object())
        with self.assertRaisesRegex(RuntimeError, "이미 시작"):
            manager.start(object(), object(), object())
        self.launch.assert_called_once()

    def test_connection_failure_has_log_path_and_terminates_only_owned_child(self):
        client = self.make_client()
        client.connect.return_value = False
        self.native.side_effect = None
        self.native.return_value = client
        manager = self.manager()
        with self.assertRaisesRegex(RuntimeError, "agent-1.log"):
            manager.start(object(), object(), object())
        output = manager.agents[0].output
        manager.close()
        self.processes[0].terminate.assert_called_once()
        self.assertTrue(output.closed)

    def test_exited_process_prevents_handshake_and_is_reaped(self):
        process = self.make_process()
        process.poll.return_value = 2
        self.launch.side_effect = None
        self.launch.return_value = process
        manager = self.manager()
        with self.assertRaisesRegex(RuntimeError, "연결에 실패"):
            manager.start(object(), object(), object())
        self.clients[0].connect.assert_not_called()
        manager.close()
        process.terminate.assert_not_called()
        process.wait.assert_called_once_with(timeout=2)

    def test_launch_failure_keeps_client_for_disconnect_and_closes_output(self):
        self.launch.side_effect = OSError("not executable")
        manager = self.manager()
        with self.assertRaises(OSError):
            manager.start(object(), object(), object())
        output = manager.agents[0].output
        manager.close()
        self.assertTrue(output.closed)
        self.clients[0].disconnect.assert_called_once()

    def test_sink_failure_is_cleaned_before_releasing_bindings(self):
        client = self.make_client()
        client.register_sink.return_value = False
        self.native.side_effect = None
        self.native.return_value = client
        manager = self.manager()
        with self.assertRaisesRegex(RuntimeError, "이벤트 연결"):
            manager.start(object(), object(), object())
        self.assertIsNotNone(manager._bindings)
        manager.close()
        self.assertIsNone(manager._bindings)

    def test_duplicate_custom_names_fail_whole_start_but_action_and_recognition_names_may_match(self):
        manager = self.manager([{"child_exec": "python"}, {"child_exec": "python"}])
        first, second = self.make_client(), self.make_client()
        first.custom_action_list = second.custom_action_list = ["SameAction"]
        self.native.side_effect = [first, second]
        with self.assertRaisesRegex(RuntimeError, "커스텀 이름"):
            manager.start(object(), object(), object())
        manager.close()
        other = self.manager([{"child_exec": "python"}, {"child_exec": "python"}])
        first, second = self.make_client(), self.make_client()
        first.custom_action_list, second.custom_recognition_list = ["Same"], ["Same"]
        self.native.side_effect = [first, second]
        other.start(object(), object(), object())

    def test_agent_exit_is_reported_before_another_task(self):
        manager = self.manager()
        manager.start(object(), object(), object())
        self.processes[0].poll.return_value = 3
        with self.assertRaisesRegex(RuntimeError, "프로세스가 종료"):
            manager.ensure_running()

    def test_unresponsive_child_gets_grace_period_then_terminate_then_kill(self):
        manager = self.manager()
        manager.start(object(), object(), object())
        process = self.processes[0]
        process.wait.side_effect = [subprocess.TimeoutExpired("agent", 3), subprocess.TimeoutExpired("agent", 2), 0]
        manager.close()
        self.assertEqual(process.wait.call_args_list, [call(timeout=3), call(timeout=2), call(timeout=2)])
        process.terminate.assert_called_once()
        process.kill.assert_called_once()

    def test_failed_disconnect_retains_sdk_owners_and_endpoint_until_retry(self):
        manager = self.manager({"child_exec": "python", "identifier": "fixed"})
        bindings = (object(), object(), object())
        manager.start(*bindings)
        self.clients[0].disconnect.return_value = False
        with self.assertRaisesRegex(RuntimeError, "연결 해제"):
            manager.close()
        self.assertEqual(manager._bindings, bindings)
        self.assertIs(manager.agents[0].client, self.clients[0])
        self.lease.return_value.close.assert_not_called()
        self.clients[0].disconnect.return_value = True
        manager.close()
        self.assertIsNone(manager._bindings)
        self.lease.return_value.close.assert_called_once()

    def test_failed_process_cleanup_retains_client_and_bindings_for_retry(self):
        manager = self.manager()
        manager.start(object(), object(), object())
        self.processes[0].wait.side_effect = OSError("access denied")
        with self.assertRaisesRegex(RuntimeError, "프로세스 정리"):
            manager.close()
        self.assertIsNotNone(manager._bindings)
        self.assertIsNotNone(manager.agents[0].client)
        self.processes[0].wait.side_effect = None
        manager.close()

    def test_failed_output_cleanup_can_retry_without_touching_destroyed_sdk_client(self):
        manager = self.manager()
        manager.start(object(), object(), object())
        original = manager.agents[0].output
        output = MagicMock()
        output.close.side_effect = OSError("busy")
        manager.agents[0].output = output
        original.close()
        with self.assertRaisesRegex(RuntimeError, "output 정리"):
            manager.close()
        self.assertIsNone(manager.agents[0].client)
        output.close.side_effect = None
        manager.close()
        self.clients[0].disconnect.assert_called_once()


class RuntimeAgentTests(AgentTestBase):
    def setUp(self):
        super().setUp()
        self.interface = interface_config()
        self.stack.enter_context(patch("app.runtime.AppRuntime._load_interface", return_value=self.interface))
        self.stack.enter_context(patch("app.runtime.create_user32", return_value=MagicMock()))
        self.runtime = AppRuntime()
        self.configure_runtime(self.runtime)
        self.addCleanup(self.runtime.release_session)

    def configure_runtime(self, runtime):
        runtime.interface_path = self.directory / "interface.json"
        runtime.user_dir = self.directory / "user"
        runtime._toolkit_initialized = True
        self.stack.enter_context(patch.object(runtime, "_resize_window_for_task", return_value=True))
        self.stack.enter_context(patch.object(runtime, "_execute_controller", return_value=(True, "connected")))
        self.stack.enter_context(patch.object(runtime, "_get_controller_log_message", return_value="connected"))
        self.stack.enter_context(patch.object(runtime, "_get_adb_execution_connection", return_value={}))
        self.stack.enter_context(patch("app.runtime.adb_controller_options", return_value={}))

        def create(*args, **kwargs):
            runtime.controller = MagicMock(connected=True)
            runtime.controller.post_inactive.return_value.wait.return_value.succeeded = True
            return True, "created"

        def load():
            if runtime.resource is None:
                runtime.resource = MagicMock(loaded=True)
                runtime._resource_loaded = True
            return True, "loaded"

        self.stack.enter_context(patch.object(runtime, "_create_controller", side_effect=create))
        self.stack.enter_context(patch.object(runtime, "_load_resource", side_effect=load))
        self.tasker_factory = self.stack.enter_context(patch("app.runtime.Tasker"))
        self.tasker_factory.side_effect = lambda: MagicMock(running=False, stopping=False, inited=True)

    def start(self, queue=None, runtime=None):
        return (runtime or self.runtime).initialize(program_settings={}, execution_queue=queue if queue is not None else [("Task", {})])

    def test_regular_execution_starts_agent_only_after_tasker_is_bound(self):
        self.assertTrue(self.start()[0])
        runtime = self.runtime
        self.assertIs(runtime._agent_session._bindings[2], runtime.tasker)
        runtime.tasker.bind.assert_called_once_with(runtime.resource, runtime.controller)
        self.clients[0].register_sink.assert_called_once_with(runtime.resource, runtime.controller, runtime.tasker)

    def test_cleanup_drains_controller_then_disconnects_agent_before_restoring_window(self):
        self.assertTrue(self.start()[0])
        runtime = self.runtime
        events = []
        runtime.tasker.running = True
        stop = runtime.tasker.post_stop.return_value
        stop.wait.return_value.succeeded = True
        stop.wait.side_effect = lambda: events.append("stop") or stop
        runtime._deactivate_controller = MagicMock(side_effect=lambda: events.append("inactive") or (True, ""))
        self.clients[0].disconnect.side_effect = lambda: events.append("disconnect") or True
        runtime._restore_startup_window_guard = MagicMock(side_effect=lambda: events.append("restore") or True)
        self.assertTrue(runtime.release_session()[0])
        self.assertEqual(events, ["stop", "inactive", "disconnect", "restore"])
        self.assertIsNone(runtime.resource)
        self.assertIsNone(runtime._agent_session)

    def test_run_and_reinitialize_never_reuse_agent_callbacks_or_resource(self):
        self.assertTrue(self.start()[0])
        old_resource = self.runtime.resource
        self.assertTrue(self.runtime.run_task([("Task", {})])[0])
        self.assertEqual(len(self.clients), 1)
        self.assertTrue(self.start()[0])
        self.assertIsNot(self.runtime.resource, old_resource)
        self.assertEqual(len(self.clients), 2)
        self.assertNotEqual(self.clients[0].identifier, self.clients[1].identifier)

    def test_no_agent_retains_existing_resource_cache_and_launch_only_never_starts_agent(self):
        self.runtime.interface.pop("agent")
        self.assertTrue(self.start()[0])
        resource = self.runtime.resource
        self.assertTrue(self.runtime.release_session()[0])
        self.assertIs(self.runtime.resource, resource)
        self.native.assert_not_called()
        self.runtime.interface["agent"] = {"child_exec": "python"}
        with patch.object(self.runtime, "_launch_program", return_value=(True, "already running")):
            self.assertTrue(self.start([(PROGRAM_LAUNCH_ENTRY, {})])[0])
        self.native.assert_not_called()

    def test_explicit_empty_queue_starts_no_agent_but_default_queue_does(self):
        self.assertTrue(self.start([])[0])
        self.native.assert_not_called()
        self.assertTrue(self.runtime.initialize(execution_queue=None)[0])
        self.native.assert_called_once()

    def test_failed_handshake_cleans_partial_start_and_rejects_tasks(self):
        client = self.make_client()
        client.connect.return_value = False
        self.native.side_effect = None
        self.native.return_value = client
        success, message = self.start()
        self.assertFalse(success)
        self.assertIn("Agent 1", message)
        self.assertIsNone(self.runtime.tasker)
        self.assertIsNone(self.runtime.controller)
        self.assertIsNone(self.runtime.resource)
        client.disconnect.assert_called_once()

    def test_invalid_declaration_or_executable_blocks_pipeline_execution(self):
        for value in ({"child_exec": "python", "child_args": "invalid"}, {"child_exec": "missing-agent"}):
            with self.subTest(value=value), patch("app.agentRuntime.shutil.which", return_value=None):
                self.runtime.interface["agent"] = value
                success, message = self.start()
                self.assertFalse(success)
                self.assertIn("Agent", message)
                self.assertIsNone(self.runtime.tasker)
        self.native.assert_not_called()
        self.launch.assert_not_called()

    def test_failed_controller_drain_does_not_disconnect_live_agent_callbacks(self):
        self.assertTrue(self.start()[0])
        with patch.object(self.runtime, "_deactivate_controller", return_value=(False, "not idle")):
            self.assertFalse(self.runtime.release_session()[0])
        self.clients[0].disconnect.assert_not_called()
        self.assertIsNotNone(self.runtime._agent_session._bindings)
        self.assertTrue(self.runtime.release_session()[0])

    def test_cleanup_failure_keeps_session_bindings_and_blocks_reinitialization(self):
        self.assertTrue(self.start()[0])
        manager = self.runtime._agent_session
        self.managers.append(manager)
        self.clients[0].disconnect.return_value = False
        self.assertFalse(self.runtime.release_session()[0])
        self.assertIs(self.runtime._agent_session, manager)
        self.assertIsNotNone(manager._bindings)
        self.assertFalse(self.start()[0])
        self.assertEqual(len(self.clients), 1)
        self.clients[0].disconnect.return_value = True
        self.assertTrue(self.runtime.release_session()[0])

    def test_dead_agent_is_reported_without_posting_another_pipeline(self):
        self.assertTrue(self.start()[0])
        tasker = self.runtime.tasker
        self.processes[0].poll.return_value = 1
        with self.assertRaisesRegex(RuntimeError, "Agent 1 프로세스가 종료"):
            self.runtime.run_task([("Task", {})])
        tasker.post_task.assert_not_called()
        self.assertIsNone(self.runtime._agent_session)

    def test_process_sessions_use_their_selected_controller_and_own_log_directory(self):
        snapshot = {"interface": deepcopy(self.interface), "resource": deepcopy(self.runtime.resource_config),
                    "interface_path": str(self.directory / "interface.json")}
        connection = {"preset": "Android", "target": {"kind": "Adb", "key": "127.0.0.1:5555"}}
        session = SessionRuntime(snapshot, connection, self.directory / "sessions" / "one")
        self.configure_runtime(session)
        session.user_dir = self.directory / "sessions" / "one"
        self.addCleanup(session.release_session)
        self.assertTrue(self.start()[0])
        self.assertTrue(self.start(runtime=session)[0])
        self.assertIsNot(self.runtime._agent_session, session._agent_session)
        self.assertNotEqual(self.clients[0].identifier, self.clients[1].identifier)
        environments = [item.kwargs["env"] for item in self.launch.call_args_list]
        self.assertEqual(json.loads(environments[0]["PI_CONTROLLER"])["type"], "Win32")
        self.assertEqual(json.loads(environments[1]["PI_CONTROLLER"])["type"], "Adb")
        self.assertTrue(session._agent_session.log_dir.is_relative_to(session.user_dir))


if __name__ == "__main__":
    unittest.main()
