"""Exercise installer wiring with real PowerShell and fake Python worker modules."""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(os.name == 'nt', 'Windows PowerShell/DPAPI installation harness')
class WindowsSetupTest(unittest.TestCase):
    def test_install_profiles_keep_secrets_out_of_actions(self):
        repo = Path(__file__).resolve().parent.parent
        for profile, scenario in (('Logs', 'Success'), ('Accounts', 'Success'), ('Full', 'Success'),
                                  ('Logs', 'UploadFailure'), ('Full', 'ExistingTask')):
            with self.subTest(profile=profile, scenario=scenario), tempfile.TemporaryDirectory(prefix='dashboard setup ') as temp:
                project = Path(temp)
                (project / 'scripts').mkdir()
                for name in ('setup_host.ps1', 'new_host_task.ps1', 'monitor_task.ps1'):
                    shutil.copyfile(repo / 'scripts' / name, project / 'scripts' / name)
                modules = project / 'collector'
                modules.mkdir()
                (modules / '__init__.py').write_text('')
                # Stubs isolate Windows task wiring from MT5, network and the real queue.
                (modules / 'onboarding.py').write_text("print('fixture proposal validated')\n")
                (modules / 'install_ea_probe.py').write_text("print('fixture compilation checked')\n")
                (modules / '__main__.py').write_text("raise SystemExit(1)\n" if scenario == 'UploadFailure' else
                                                    "from pathlib import Path\nPath('collector-central.db').touch()\n")
                proposals = project / 'host-setup'
                proposals.mkdir()
                (proposals / 'plan.json').write_text(json.dumps({
                    'version': 1, 'hostname': socket.gethostname(), 'host_id': 'fixture-host',
                    'profile': profile, 'python': sys.executable, 'server_url': 'https://example.com',
                    'roots': [], 'terminals': [],
                }))
                (proposals / 'inventory.json').write_text(json.dumps({'expected': [str(project)], 'archived': []}))
                if profile != 'Logs':
                    (proposals / 'accounts.json').write_text(json.dumps({'terminals': [{
                        'data_path': str(project), 'login': 123, 'server': 'Fixture',
                    }]}))
                result = subprocess.run([
                    'powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                    str(repo / 'tests' / 'windows_setup_harness.ps1'), '-ProjectDir', str(project),
                    '-PythonExe', sys.executable, '-Profile', profile, '-Scenario', scenario,
                ], capture_output=True, text=True, timeout=45, check=False)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                expected = 'Fixture installation passed: ' + profile if scenario == 'Success' else 'Fixture refusal passed: ' + scenario
                self.assertIn(expected, result.stdout)


if __name__ == '__main__':
    unittest.main()
