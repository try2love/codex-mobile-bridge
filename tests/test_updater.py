import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import bridge.features.updates.gateway as updater


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / '.tmp'
        root.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def unpack(self, entries):
        archive = self.root / 'update.zip'
        with zipfile.ZipFile(archive, 'w') as target:
            for name, value, mode in entries:
                info = zipfile.ZipInfo('entry')
                # Preserve malformed wire names; ZipInfo(name) normalizes them
                # on Windows before the test archive is even written.
                info.filename = name
                info.orig_filename = name
                info.external_attr = mode << 16
                target.writestr(info, value)
        dest = self.root / 'unpacked'
        dest.mkdir(exist_ok=True)
        updater.extract(archive, dest)
        return dest

    def test_reject_path_traversal_before_writing(self):
        for name in ('../escape', '/absolute', 'x/../../escape', 'x\\escape', 'C:/escape', 'x/./y', 'x\0escape'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.unpack([(name, 'bad', stat.S_IFREG | 0o644)])
        self.assertFalse((self.root / 'escape').exists())

    def test_reject_external_links_and_writes_through_links(self):
        if updater.sys.platform == 'win32':
            self.skipTest('Windows rejects all archive symlinks')
        for entries in ([('link', '../outside', stat.S_IFLNK | 0o777)],
                        [('folder/link', '/outside', stat.S_IFLNK | 0o777)],
                        [('link', 'folder', stat.S_IFLNK | 0o777), ('link/file', 'bad', stat.S_IFREG | 0o644)]):
            with self.assertRaises(ValueError):
                self.unpack(entries)

    def test_reject_case_collisions_and_special_files(self):
        with self.assertRaises(ValueError):
            self.unpack([('file', 'a', stat.S_IFREG | 0o644), ('FILE', 'b', stat.S_IFREG | 0o644)])
        with self.assertRaises(ValueError):
            self.unpack([('socket', '', stat.S_IFSOCK | 0o644)])

    @unittest.skipIf(updater.sys.platform == 'win32', 'Unix executable permissions and symlinks')
    def test_preserve_executable_and_internal_framework_symlink(self):
        dest = self.unpack([('Versions/A/Executable', 'program', stat.S_IFREG | 0o755),
                            ('Versions/Current', 'A', stat.S_IFLNK | 0o777),
                            ('Executable', 'Versions/Current/Executable', stat.S_IFLNK | 0o777)])
        self.assertEqual((dest / 'Executable').read_text(), 'program')
        self.assertTrue((dest / 'Executable').stat().st_mode & stat.S_IXUSR)


class TransactionTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / '.tmp'
        root.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.target, self.staged, self.backup = (self.root / name for name in ('app', 'staged', 'previous'))
        for directory, version in ((self.target, 'old'), (self.staged, 'new')):
            directory.mkdir()
            (directory / 'version').write_text(version)
        self.data = self.root / 'data'; self.data.mkdir()
        self.configs = ['config.json', 'desktop.json', 'notifications.json', 'notification-watches.json']
        for name in self.configs:
            (self.data / name).write_text('preserve exactly: ' + name)
        self.state = {'running': True, 'portOccupied': False, 'instanceId': 'old'}
        self.calls = []
        outer = self
        class Desktop:
            def status(self):
                return dict(outer.state)
            def stop(self):
                outer.calls.append('stop')
                outer.state.update(running=False, instanceId=None)
        self.desktop = Desktop()
        self.plan = {'target': str(self.target), 'staged': str(self.staged), 'backup': str(self.backup),
                     'dataDir': str(self.data), 'version': '0.2.0-beta.6', 'parentPid': 999999,
                     'token': 'test', 'runtime': dict(self.state)}
        self.plan_file = self.root / 'plan.json'
        self.plan_file.write_text(json.dumps(self.plan))
        self.addCleanup(self.check_configs)

    def check_configs(self):
        for name in self.configs:
            self.assertEqual((self.data / name).read_text(), 'preserve exactly: ' + name)

    def start(self, target, data_dir, desktop):
        self.calls.append('start-' + (target / 'version').read_text())
        self.state.update(running=True, instanceId='new')

    def launch(self, target, transaction, plan, acknowledge=True):
        self.calls.append('launch-' + (target / 'version').read_text())
        class Child:
            def poll(self): return None
            def terminate(self): pass
            def wait(self, timeout): pass
        return Child()

    def run_update(self, health=lambda *args: None, wait=lambda pid: None, desktop=None):
        with patch.object(updater, 'registry_version'):
            return updater.apply(self.plan_file, desktop=desktop or self.desktop, wait=wait,
                                 launch=self.launch, health=health, start=self.start)

    def test_success_stops_and_restarts_gateway_and_keeps_backup(self):
        result = self.run_update()
        self.assertEqual(result['state'], 'updated')
        self.assertEqual(self.calls, ['stop', 'launch-new', 'start-new'])
        self.assertEqual((self.target / 'version').read_text(), 'new')
        self.assertEqual((self.backup / 'version').read_text(), 'old')

    def test_unhealthy_app_rolls_back_before_restoring_gateway(self):
        def fail(*args): raise RuntimeError('health failed')
        result = self.run_update(health=fail)
        self.assertTrue(result['recovered'])
        self.assertEqual(result['state'], 'failed')
        self.assertEqual((self.target / 'version').read_text(), 'old')
        self.assertEqual(self.calls, ['stop', 'launch-new', 'start-old', 'launch-old'])

    def test_parent_timeout_never_stops_or_replaces_running_app(self):
        def fail(pid): raise TimeoutError('still running')
        self.assertEqual(self.run_update(wait=fail)['state'], 'failed')
        self.assertEqual(self.calls, [])
        self.assertEqual((self.target / 'version').read_text(), 'old')

    def test_changed_gateway_instance_is_not_stopped(self):
        self.state['instanceId'] = 'someone-else'
        self.assertEqual(self.run_update()['state'], 'failed')
        self.assertNotIn('stop', self.calls)
        self.assertEqual((self.target / 'version').read_text(), 'old')

    def test_health_timeout_after_identity_check_still_sends_stop(self):
        outer = self
        probes = iter([dict(self.state), {'running': False, 'pid': None}, {'running': False, 'pid': None}])
        class Desktop:
            def status(self): return next(probes, dict(outer.state))
            def stop(self):
                outer.calls.append('stop')
                outer.state.update(running=False, instanceId=None)
        self.assertEqual(self.run_update(desktop=Desktop())['state'], 'updated')
        self.assertIn('stop', self.calls)

    def test_health_timeout_is_not_exit_proof_for_verified_pid(self):
        outer = self
        probes = iter([{**self.state, 'pid': 424242}])
        class Desktop:
            def status(self): return next(probes, {'running': False, 'pid': None})
            def stop(self): outer.calls.append('stop')
        with patch.object(updater, 'process_exists', return_value=True), patch.object(updater, 'STOP_RETRY_SECONDS', .01):
            result = self.run_update(desktop=Desktop())
        self.assertEqual(result['state'], 'failed')
        self.assertNotIn('launch-new', self.calls)
        self.assertEqual((self.target / 'version').read_text(), 'old')

    def test_transient_gateway_status_is_retried(self):
        delayed = iter([{'running': False, 'portOccupied': False, 'instanceId': None}, None])
        outer = self
        class Desktop:
            def status(self):
                value = next(delayed, None)
                return value or dict(outer.state)
            def stop(self):
                outer.calls.append('stop')
                outer.state.update(running=False, instanceId=None)
        result = self.run_update(desktop=Desktop())
        self.assertEqual(result['state'], 'updated')
        self.assertEqual(self.calls, ['stop', 'launch-new', 'start-new'])

    def test_gateway_stop_retry_survives_first_ambiguous_probe(self):
        normal = self.start
        def start(target, data_dir, desktop):
            if (target / 'version').read_text() == 'new': normal(target, data_dir, desktop)
        self.start = start
        probes = iter([None, self.state, self.state, {'running': False, 'pid': None}])
        outer = self
        class Desktop:
            def status(self):
                value = next(probes, {'running': False, 'pid': None})
                return dict(value or outer.state)
            def stop(self):
                outer.calls.append('stop')
                outer.state.update(running=False, instanceId=None)
        with patch.object(updater, 'STOP_RETRY_SECONDS', .1):
            result = self.run_update(desktop=Desktop())
        self.assertEqual(result['state'], 'updated')
        self.assertEqual(self.calls, ['stop', 'launch-new', 'start-new'])

    def test_gateway_stop_failure_does_not_swap_or_leave_gateway_stopped(self):
        outer = self
        class Desktop:
            def status(self): return dict(outer.state)
            def stop(self): outer.calls.append('stop-failed'); raise RuntimeError('control timeout')
        with patch.object(updater, 'STOP_RETRY_SECONDS', .1):
            result = self.run_update(desktop=Desktop())
        self.assertEqual(result['state'], 'failed')
        self.assertIn('网关尚未停止', result['message'])
        self.assertNotIn('launch-new', self.calls)
        self.assertTrue(self.state['running'])

    def test_failed_update_keeps_durable_journal_after_transaction_cleanup(self):
        def fail(*args): raise RuntimeError('health failed')
        result = self.run_update(health=fail)
        rows = [json.loads(line) for line in (self.data/'desktop-update.log').read_text().splitlines()]
        self.assertEqual(rows[-1]['state'], 'failed')
        self.assertEqual(rows[-1]['message'], 'health failed')
        self.assertEqual(rows[-1]['target'], str(self.target))
        self.assertTrue(result['recovered'])

    def test_pending_transaction_is_rejected_until_helper_exits(self):
        transaction = self.root / '.cmb-update-pending'
        transaction.mkdir()
        updater.write_json(transaction/'plan.json', {'target': str(self.target)})
        (transaction/'helper.json').write_text(json.dumps({'pid': updater.os.getpid()}))
        with self.assertRaisesRegex(ValueError, '上一次更新事务尚未完成'):
            updater.reject_pending_transaction(self.target)

    def test_stale_pending_transaction_is_removed(self):
        transaction = self.root / '.cmb-update-stale'
        transaction.mkdir()
        updater.write_json(transaction/'plan.json', {'target': str(self.target)})
        updater.os.utime(transaction, (0, 0))
        updater.reject_pending_transaction(self.target)
        self.assertFalse(transaction.exists())

    def test_stale_live_pid_record_is_removed(self):
        transaction = self.root / '.cmb-update-reused-pid'
        transaction.mkdir()
        updater.write_json(transaction/'plan.json', {'target': str(self.target)})
        (transaction/'helper.json').write_text(json.dumps({'pid': updater.os.getpid()}))
        updater.os.utime(transaction, (0, 0))
        updater.reject_pending_transaction(self.target)
        self.assertFalse(transaction.exists())

    def test_recovered_transactions_are_cleaned_but_unresolved_failures_remain(self):
        recovered = self.root / '.cmb-update-recovered'
        unresolved = self.root / '.cmb-update-unresolved'
        recovered.mkdir(); unresolved.mkdir()
        for transaction in (recovered, unresolved):
            updater.write_json(transaction/'plan.json', {'target': str(self.target)})
        updater.write_json(recovered/'result.json', {'state': 'failed', 'recovered': True})
        updater.write_json(unresolved/'result.json', {'state': 'failed', 'recovered': False})
        updater.clean_transactions(self.target)
        self.assertFalse(recovered.exists())
        self.assertTrue(unresolved.exists())

    def test_crashed_swap_preserves_backup_even_with_dead_or_stale_helper(self):
        transaction = self.root / '.cmb-update-crashed'
        (transaction/'previous').mkdir(parents=True)
        backup = transaction/'previous/version'
        backup.write_text('old')
        updater.write_json(transaction/'plan.json', {'target': str(self.target)})
        updater.write_json(transaction/'helper.json', {'pid': 999999})
        for stale in (False, True):
            if stale:
                updater.os.utime(transaction, (0, 0))
            with patch.object(updater, 'process_exists', return_value=False):
                with self.assertRaisesRegex(ValueError, '已保留备份'):
                    updater.reject_pending_transaction(self.target)
            updater.clean_transactions(self.target)
            self.assertEqual(backup.read_text(), 'old')

    def test_started_helper_without_result_is_never_discarded_by_age(self):
        transaction = self.root / '.cmb-update-started'
        transaction.mkdir()
        updater.write_json(transaction/'plan.json', {'target': str(self.target)})
        updater.write_json(transaction/'ready.json', {'token': 'started'})
        updater.write_json(transaction/'helper.json', {'pid': updater.os.getpid()})
        updater.os.utime(transaction, (0, 0))
        with self.assertRaisesRegex(ValueError, '已保留备份'):
            updater.reject_pending_transaction(self.target)
        self.assertTrue((transaction/'ready.json').exists())

    def test_other_installations_and_unknown_ownership_are_untouched(self):
        for name, plan in (('other', {'target': str(self.root/'other-app')}), ('unknown', {})):
            transaction = self.root / ('.cmb-update-' + name)
            transaction.mkdir()
            updater.write_json(transaction/'plan.json', plan)
            updater.write_json(transaction/'helper.json', {'pid': updater.os.getpid()})
            updater.reject_pending_transaction(self.target)
            updater.os.utime(transaction, (0, 0))
            updater.reject_pending_transaction(self.target)
            updater.write_json(transaction/'result.json', {'state': 'updated'})
            updater.clean_transactions(self.target)
            self.assertTrue(transaction.exists())

    def test_unknown_result_state_keeps_backup(self):
        transaction = self.root / '.cmb-update-unknown-result'
        (transaction/'previous').mkdir(parents=True)
        updater.write_json(transaction/'plan.json', {'target': str(self.target)})
        updater.write_json(transaction/'result.json', {'state': 'unknown'})
        updater.clean_transactions(self.target)
        self.assertTrue((transaction/'previous').is_dir())

    def test_stopped_gateway_remains_stopped(self):
        self.state.update(running=False, instanceId=None)
        self.plan['runtime'] = dict(self.state)
        self.plan_file.write_text(json.dumps(self.plan))
        self.run_update()
        self.assertEqual(self.calls, ['launch-new'])
        self.assertFalse(self.state['running'])

    def test_app_exit_and_worker_errors_are_reported_with_diagnostics(self):
        class Child:
            def poll(self): return 2
            def terminate(self): pass
            def wait(self, timeout): pass
        transaction = self.root / 'transaction';transaction.mkdir()
        (transaction/'new-app.log').write_text('Electron failed: missing framework', encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, 'exit 2.*missing framework'):
            updater.check_app(Child(), transaction, self.plan)
        original_locations = updater.locations
        class Command:
            returncode = 1
            stdout = b'{"ok":false,"error":"cloudflared is missing"}'
            stderr = b'secondary detail'
        def fake_locations(target):
            if target == self.target: return original_locations(target)
            return (self.target/'unused', self.target/'unused-worker', self.target)
        with patch.object(updater, 'locations', fake_locations), \
             patch.object(updater.subprocess, 'run', return_value=Command()):
            with self.assertRaisesRegex(RuntimeError, 'cloudflared is missing'):
                updater.start_gateway(self.target, str(self.data), self.desktop)

    def test_failed_gateway_start_restores_original_app_and_gateway(self):
        normal = self.start
        def start(target, data_dir, desktop):
            if (target / 'version').read_text() == 'new':
                raise RuntimeError('new gateway failed')
            normal(target, data_dir, desktop)
        self.start = start
        result = self.run_update()
        self.assertTrue(result['recovered'])
        self.assertEqual((self.target / 'version').read_text(), 'old')
        self.assertIn('start-old', self.calls)


if __name__ == '__main__':
    unittest.main()
