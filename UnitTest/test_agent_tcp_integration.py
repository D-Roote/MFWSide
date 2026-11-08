"""Real Agent handshake/actions in a subprocess; no external device access."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class AgentTcpIntegrationTests(unittest.TestCase):
    def test_real_parallel_agents_actions_disconnect_and_fixed_port_reconnect(self):
        cache = ROOT / '.cache'
        cache.mkdir(exist_ok=True)
        directory = Path(tempfile.mkdtemp(prefix='agent-tcp-integration-', dir=cache))
        result = subprocess.run([sys.executable, str(ROOT / 'tools/verify_agent_tcp.py'),
                                 '--directory', str(directory)],
                                capture_output=True, text=True, timeout=45, cwd=ROOT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads((directory / 'result.json').read_text(encoding='utf-8'))
        self.assertTrue(report['ok'])
        self.assertTrue(report['fixed_port_reconnect'])
        self.assertTrue(report['peer_survived_disconnect'])
        self.assertTrue(report['clean_agent_exit'])
        self.assertFalse(report['filesystem_ipc_in_trace'])
        self.assertEqual(len({session['port'] for session in report['sessions']}), 2)
        self.assertTrue(all(session['action'] for session in report['sessions']))


if __name__ == '__main__':
    unittest.main()
