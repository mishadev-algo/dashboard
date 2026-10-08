from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from collector.accounts import main as accounts_main
from collector.__main__ import main as collector_main
from collector.diagnose import delivery_findings, diagnose
from collector.core import open_database
from collector.mt5_identity import identity
from collector.onboarding import inventory_proposal, validate_proposals
from collector.process import ProcessSnapshot
from collector.remote import RemoteUploadError
from shared.paths import windows_path
from shared.runtime_state import read_state, write_state


class OnboardingTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.active = self.root / 'ACTIVE'
        self.old = self.root / 'OLD'
        for path, install in ((self.active, r'C:\Apps\Live'), (self.old, r'C:\Apps\Old')):
            (path / 'Logs').mkdir(parents=True)
            (path / 'origin.txt').write_text(install, encoding='utf-16')
        self.now = datetime.now(timezone.utc)
        self.processes = ProcessSnapshot(((10, r'C:\Apps\Live\terminal64.exe'),), True, self.now.isoformat())
        self.inventory = self.root / 'inventory.json'
        self.inventory.write_text(json.dumps({'expected': [str(self.active)], 'archived': [str(self.old)]}))
        self.accounts = self.root / 'accounts.json'
        self.accounts.write_text(json.dumps({'terminals': [{
            'data_path': str(self.active), 'login': 123, 'server': 'Broker', 'day_timezone': 'Etc/GMT-3',
        }]}))

    def test_discovery_separates_running_and_stopped_and_reports_unmatched(self):
        inventory, issues = inventory_proposal([self.root], [], self.processes)
        self.assertEqual(inventory['expected'], [str(self.active.resolve())])
        self.assertEqual(inventory['archived'], [str(self.old.resolve())])
        self.assertEqual(issues, [])
        unknown = ProcessSnapshot(((11, r'D:\Elsewhere\terminal64.exe'),), True, self.now.isoformat())
        _, issues = inventory_proposal([self.root], [], unknown)
        self.assertTrue(any('PID 11' in issue for issue in issues))

    def test_inaccessible_processes_do_not_become_archived(self):
        incomplete = ProcessSnapshot(((10, None),), False, self.now.isoformat())
        inventory, issues = inventory_proposal([self.root], [], incomplete)
        self.assertEqual(inventory['archived'], [])
        self.assertTrue(issues)

    def test_validation_rejects_wrong_account_and_incomplete_process_visibility(self):
        with patch('collector.onboarding.list_terminal_processes', return_value=self.processes), patch(
            'collector.onboarding.account_identity', return_value={'login': 999, 'server': 'Broker'}):
            with self.assertRaisesRegex(ValueError, 'Account mismatch'):
                validate_proposals(self.inventory, self.accounts)
        with patch('collector.onboarding.list_terminal_processes', return_value=ProcessSnapshot((), False, 'now')):
            with self.assertRaisesRegex(ValueError, 'inaccessible'):
                validate_proposals(self.inventory, None)

    def test_receipt_uses_acknowledgement_not_queue_size_and_rejects_wrong_host(self):
        state = {'host_id': 'vps', 'server_url': 'https://example.com',
                 'last_scan_utc': self.now.isoformat(), 'last_upload_utc': self.now.isoformat(), 'pending': 17}
        self.assertEqual(delivery_findings(state, 'vps', 'https://example.com', self.now), [])
        state['pending'] = 0
        state['last_upload_utc'] = (self.now - timedelta(minutes=20)).isoformat()
        self.assertTrue(delivery_findings(state, 'vps', 'https://example.com', self.now))
        state['last_upload_utc'] = self.now.isoformat()
        self.assertTrue(delivery_findings(state, 'other', 'https://example.com', self.now))
        state['upload_error'] = 'failed'
        self.assertTrue(delivery_findings(state, 'vps', 'https://example.com', self.now))

    def test_wrong_terminal_is_rejected_and_shutdown_called(self):
        mt5 = SimpleNamespace(initialize=lambda *a, **kw: True,
                              terminal_info=lambda: SimpleNamespace(data_path='C:\\Wrong', path='C:\\Apps\\Live'),
                              shutdown=lambda: calls.append('shutdown'))
        calls = []
        result = identity(mt5, str(self.active), r'C:\Apps\Live\terminal64.exe')
        self.assertIn('different terminal', result['error'])
        self.assertEqual(calls, ['shutdown'])

    def test_failed_account_upload_does_not_get_successful_receipt(self):
        status = self.root / '.dashboard-accounts-state.json'
        payload = {'observed_at_utc': self.now.isoformat(), 'status': {'state': 'ok', 'data_complete': True}}
        arguments = ['accounts', '--config', str(self.accounts), '--host-id', 'vps',
                     '--server-url', 'https://example.com', '--status-file', str(status)]
        with patch('sys.argv', arguments), patch.dict('os.environ', DASHBOARD_COLLECTOR_TOKEN='token'), patch(
            'collector.accounts.list_terminal_processes', return_value=self.processes), patch(
            'collector.accounts.probe_target', return_value=payload), patch(
            'collector.accounts.upload_snapshot', side_effect=RuntimeError('upload failed')), redirect_stdout(io.StringIO()):
            accounts_main()
        receipt = read_state(status)['terminals'][windows_path(str(self.active))]
        self.assertNotIn('last_upload_utc', receipt)
        self.assertIn('upload_error', receipt)

    def test_diagnosis_idle_logs_need_fresh_heartbeat_and_no_mutation(self):
        db = self.root / 'collector.db'
        with closing(open_database(db)) as connection:
            with connection:
                connection.execute('INSERT INTO terminals VALUES (?,?,?,?)', ('t', 'vps', str(self.active), self.now.isoformat()))
        settings = self.root / '.dashboard-remote.local.json'
        write_state(settings, {'inventory': 'inventory.json', 'collector_db': 'collector.db',
                               'server_url': 'https://example.com', 'collector_token': 'DO_NOT_PRINT'})
        write_state(self.root / '.dashboard-collector-state.json', {
            'host_id': 'vps', 'server_url': 'https://example.com', 'last_scan_utc': self.now.isoformat(),
            'last_upload_utc': self.now.isoformat(), 'expected': [str(self.active)]})
        before = db.read_bytes()
        with patch('collector.diagnose.list_terminal_processes', return_value=self.processes), patch(
            'collector.diagnose.task_states', return_value={'MT5CollectorRemote': 'Running'}):
            report = diagnose(self.root, 'Logs')
        self.assertTrue(report['ready'])
        self.assertEqual(report['pending'], 0)
        self.assertNotIn('DO_NOT_PRINT', json.dumps(report))
        self.assertEqual(db.read_bytes(), before)

    def test_collector_heartbeat_receipt_requires_success_even_for_empty_queue(self):
        for succeeds in (True, False):
            with self.subTest(succeeds=succeeds):
                status = self.root / ('state-success.json' if succeeds else 'state-failure.json')
                arguments = ['collector', '--db', str(self.root / 'queue.db'), '--inventory', str(self.inventory),
                             '--host-id', 'vps', '--root', str(self.root), '--server-url', 'https://example.com',
                             '--status-file', str(status)]
                with patch('sys.argv', arguments), patch('collector.__main__.RemoteUploader') as uploader, patch(
                    'collector.__main__.ProcessProbe'), redirect_stdout(io.StringIO()):
                    if succeeds:
                        uploader.return_value.upload.return_value = 0
                        collector_main()
                    else:
                        uploader.return_value.upload.side_effect = RemoteUploadError('bad acknowledgement')
                        with self.assertRaises(SystemExit):
                            collector_main()
                receipt = read_state(status)
                self.assertEqual('last_upload_utc' in receipt, succeeds)
                self.assertEqual(receipt['pending'], 0)


if __name__ == '__main__':
    unittest.main()
