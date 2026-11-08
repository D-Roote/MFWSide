"""Exercise real session-owned Agents using synthetic images and no devices."""
import argparse
import gc
import json
from pathlib import Path
import re
import sys
import time


def wait_job(job, seconds=10):
    deadline = time.monotonic() + seconds
    while not job.done and time.monotonic() < deadline:
        time.sleep(.01)
    if not job.done or not job.succeeded:
        raise RuntimeError(f'Probe job failed or timed out: {job.job_id}')


def server(marker, action, identifier):
    from maa.agent.agent_server import AgentServer
    from maa.custom_action import CustomAction
    from maa.tasker import Tasker

    marker = Path(marker)
    Tasker.set_log_dir(marker.parent / 'server-debug')

    class ProbeAction(CustomAction):
        def run(self, context, argv):
            with marker.open('a', encoding='utf-8') as stream:
                stream.write('action\n')
            return True

    if not identifier.isascii() or not identifier.isdecimal() or not 1 <= int(identifier) <= 65535:
        raise RuntimeError('Agent must receive the actual TCP port')
    if not AgentServer.register_custom_action(action, ProbeAction()) or not AgentServer.start_up(identifier):
        raise RuntimeError('Probe Agent failed to start')
    marker.write_text(identifier + '\n', encoding='utf-8')
    try:
        AgentServer.join()
    finally:
        AgentServer.shut_down()


def verify(directory, python, script):
    import numpy as np
    from maa.controller import CustomController
    from maa.resource import Resource
    from maa.tasker import Tasker
    from maa.toolkit import Toolkit
    from app.agentRuntime import AgentSession, normalize_agents

    class MemoryController(CustomController):
        def connect(self):
            return True

        def request_uuid(self):
            return 'memory-only-tcp-probe'

        def screencap(self):
            return np.zeros((720, 1280, 3), dtype=np.uint8)

        def unexpected_input(self, *args):
            raise AssertionError('The TCP probe must not perform device input')

        start_app = stop_app = click = swipe = unexpected_input
        touch_down = touch_move = touch_up = unexpected_input
        click_key = input_text = key_down = key_up = unexpected_input

    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    if not Toolkit.init_option(directory / 'client', {'stdout_level': 0, 'save_on_error': False}):
        raise RuntimeError('Toolkit initialization failed')
    socket_directory = Path('C:/Temp')
    before = {path.name for path in socket_directory.glob('maafw-agent-*.sock')}
    results, sessions = [], []
    try:
        # Each session owns separate native bindings, just like runtime workers.
        for index, requested in enumerate((None, 'legacy-probe-name')):
            root = directory / str(index)
            root.mkdir()
            action = f'TcpProbe{index}'
            resource_dir = root / 'resource'
            (resource_dir / 'pipeline').mkdir(parents=True)
            (resource_dir / 'pipeline' / 'probe.json').write_text(json.dumps({
                'Probe': {'recognition': {'type': 'DirectHit'},
                          'action': {'type': 'Custom', 'param': {'custom_action': action}}},
            }), encoding='utf-8')
            resource, controller, tasker = Resource(), MemoryController(), Tasker()
            wait_job(resource.post_bundle(resource_dir))
            wait_job(controller.post_connection())
            if not tasker.bind(resource, controller):
                raise RuntimeError('Probe tasker binding failed')
            marker = root / 'marker.txt'
            declaration = {'child_exec': str(python), 'child_args': [str(script), '--server', str(marker), action]}
            if requested is not None:
                declaration['identifier'] = requested
            manager = AgentSession(normalize_agents(declaration), root / 'interface.json', root / 'user', {}, {}, {})
            sessions.append((manager, resource, controller, tasker))
            manager.start(resource, controller, tasker)
            client = manager.agents[0].client
            assert action in client.custom_action_list and client.alive
            wait_job(tasker.post_task('Probe'))
            lines = marker.read_text(encoding='utf-8').splitlines()
            assert lines == [client.identifier, 'action'], lines
            results.append({'port': int(client.identifier), 'requested': requested, 'action': True})
        assert results[0]['port'] != results[1]['port']
        first, second = sessions[0], sessions[1]
        first_process = first[0].agents[0].process
        first[0].close()
        assert first_process.poll() == 0
        # Closing one Agent must not interrupt the other session.
        wait_job(second[3].post_task('Probe'))
        second_process = second[0].agents[0].process
        second[0].close()
        assert second_process.poll() == 0
        # Reconnect using an explicitly fixed, zero-padded port after release.
        from dataclasses import replace
        executable, spec = first[0].commands[0]
        first[0].commands[0] = (executable, replace(spec, identifier='0' + str(results[0]['port'])))
        first[0].start(first[1], first[2], first[3])
        assert first[0].agents[0].client.identifier == str(results[0]['port'])
        wait_job(first[3].post_task('Probe'))
        fixed_process = first[0].agents[0].process
        first[0].close()
        assert fixed_process.poll() == 0
    finally:
        for manager, resource, controller, tasker in reversed(sessions):
            if tasker.running:
                wait_job(tasker.post_stop())
            wait_job(controller.post_inactive())
            manager.close()
        sessions.clear()
        gc.collect()
    trace = (directory / 'client' / 'debug' / 'maafw.log').read_text(encoding='utf-8')
    assert 'ipc://C:/Temp/maafw-agent-' not in trace
    addresses = sorted(set(re.findall(r'tcp://127\.0\.0\.1:\d+', trace)))
    assert len(addresses) >= 2, addresses
    return {'ok': True, 'sessions': results, 'fixed_port_reconnect': True,
            'peer_survived_disconnect': True, 'clean_agent_exit': True,
            'tcp_addresses': addresses, 'filesystem_ipc_in_trace': False,
            'new_socket_files_during_probe': sorted({p.name for p in socket_directory.glob('maafw-agent-*.sock')} - before)}


def main():
    if sys.argv[1:2] == ['--server']:
        server(*sys.argv[2:])
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True, type=Path)
    parser.add_argument('--python', type=Path, default=Path(sys.executable))
    options = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    result = verify(options.directory, options.python.resolve(), Path(__file__).resolve())
    (options.directory / 'result.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
