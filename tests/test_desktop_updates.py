import unittest
import uuid
import subprocess
from unittest.mock import patch, Mock

import test_accounts
from bridge.features.updates.codex import appcast, SPARKLE


class UpdateTests(unittest.TestCase):
    setUp=test_accounts.AccountsTests.setUp
    tearDown=test_accounts.AccountsTests.tearDown

    def feed(self,items):
        return ('<rss xmlns:sparkle="'+SPARKLE[1:-1]+'"><channel>'+items+'</channel></rss>').encode()

    def test_public_appcast_filters_os_channel_and_upgrade_requirements(self):
        def item(build,extra=''):
            return f'<item><sparkle:version>{build}</sparkle:version>{extra}<enclosure url="https://example.test/app.zip" /></item>'
        raw=self.feed(item('2')+item('5','<sparkle:minimumSystemVersion>99.0</sparkle:minimumSystemVersion>')+
                      item('4','<sparkle:channel>beta</sparkle:channel>')+item('3','<sparkle:minimumUpdateVersion>2</sparkle:minimumUpdateVersion>'))
        self.assertEqual(appcast(raw,'1','15.0')['targetBuild'],'2')
        self.assertIsNone(appcast(self.feed(item('1')),'1','15.0'))

    def test_request_requires_confirmation_matching_version_and_no_tasks(self):
        updater=self.manager.updates
        payload={'requestId':str(uuid.uuid4()),'confirmed':True,'tasksConfirmed':True,'currentBuild':'1'}
        with self.assertRaises(ValueError):updater.request({**payload,'confirmed':False})
        updater.value={'state':'available','canRequest':True}
        with patch('bridge.features.updates.codex.installed',return_value={'build':'2'}),patch('bridge.features.updates.codex.sys.platform','darwin'):
            with self.assertRaises(ValueError):updater.request(payload)
        with patch.object(self.manager,'idle',side_effect=ValueError('task active')):
            with self.assertRaisesRegex(ValueError,'task active'):updater.request(payload)

    def test_duplicate_authorization_is_not_replayed(self):
        updater=self.manager.updates;updater.value={'state':'available','canRequest':True}
        value={'requestId':str(uuid.uuid4()),'confirmed':True,'tasksConfirmed':True,'currentBuild':'1'}
        with patch('bridge.features.updates.codex.installed',return_value={'build':'1','bundle':'/fixture.app'}),patch('bridge.features.updates.codex.sys.platform','darwin'),patch('bridge.features.updates.codex.threading.Thread') as worker:
            updater.request(value);updater.request(value);worker.assert_called_once()

    def test_native_handoff_never_reports_installation_complete(self):
        updater=self.manager.updates
        with patch('bridge.features.updates.codex.sys.platform','darwin'), patch('bridge.features.updates.codex.subprocess.run') as run, patch('bridge.features.updates.codex.DesktopApp') as app:
            app.return_value.processes.return_value=[1234]
            run.return_value.stdout='opened\n'
            updater._request({'bundle':'/fixture.app'})
            run.assert_called_once()
            self.assertEqual(run.call_args.args[0][-1],'1234')
            self.assertEqual(run.call_args.kwargs['timeout'],180)
        self.assertEqual(updater.status()['state'],'needsDesktop')
        self.assertFalse(updater.status()['canInstall'])
        self.assertNotIn('installation', updater.status())
        self.assertNotIn('kill',str(run.call_args))
        with patch('bridge.features.updates.codex.sys.platform','darwin'), patch('bridge.features.updates.codex.subprocess.run',side_effect=OSError('private path')) as run, patch('bridge.features.updates.codex.DesktopApp') as app:
            app.return_value.processes.return_value=[1234]
            updater._request({'bundle':'/fixture.app'})
            run.assert_called_once()
        self.assertEqual(updater.status()['state'],'needsDesktop')
        self.assertNotIn('private path',str(updater.status()))

    def test_native_failures_are_specific_sanitized_and_retryable(self):
        updater=self.manager.updates
        for error,reason in [
            (subprocess.TimeoutExpired('osascript',180),'timeout'),
            (subprocess.CalledProcessError(1,'osascript',stderr='private path (-1743)'),'automationPermission'),
            (subprocess.CalledProcessError(1,'osascript',stderr='private path (-25211)'),'accessibilityPermission'),
            (subprocess.CalledProcessError(1,'osascript',stderr='BRIDGE_UPDATE_MENU_MISSING (-2700)'),'menuUnavailable'),
            (subprocess.CalledProcessError(1,'osascript',stderr='BRIDGE_UPDATE_MENU_DISABLED (-2700)'),'menuDisabled'),
            (OSError('private path'),'handoffFailed')]:
            with self.subTest(reason=reason),patch('bridge.features.updates.codex.sys.platform','darwin'),patch('bridge.features.updates.codex.DesktopApp') as app,patch('bridge.features.updates.codex.subprocess.run',side_effect=error):
                app.return_value.processes.return_value=[1234]
                updater._request({'bundle':'/fixture.app'})
                self.assertEqual(updater.status()['failureReason'],reason)
                self.assertTrue(updater.status()['canRequest'])
                self.assertNotIn('private path',str(updater.status()))

    def test_retry_uses_new_authorization_and_success_requires_menu_click_acknowledgment(self):
        updater=self.manager.updates
        with patch('bridge.features.updates.codex.sys.platform','darwin'),patch('bridge.features.updates.codex.DesktopApp') as app,patch('bridge.features.updates.codex.subprocess.run') as run:
            app.return_value.processes.return_value=[1234]
            run.return_value.stdout=''
            updater._request({'bundle':'/fixture.app'})
            self.assertTrue(updater.status()['canRequest'])
            run.return_value.stdout='opened\n'
            updater._request({'bundle':'/fixture.app'})
            self.assertFalse(updater.status()['canRequest'])
            self.assertIsNone(updater.status().get('failureReason'))
        updater.value.update(canRequest=True)
        payload={'requestId':str(uuid.uuid4()),'confirmed':True,'tasksConfirmed':True,'currentBuild':'1'}
        with patch('bridge.features.updates.codex.installed',return_value={'build':'1','bundle':'/fixture.app'}),patch('bridge.features.updates.codex.sys.platform','darwin'),patch('bridge.features.updates.codex.threading.Thread') as worker:
            updater.request(payload)
            updater.value.update(state='needsDesktop',canRequest=True)
            updater.request(payload)
            self.assertEqual(worker.call_count,1)
            updater.request({**payload,'requestId':str(uuid.uuid4())})
            self.assertEqual(worker.call_count,2)

    def test_check_failure_is_not_up_to_date_and_does_not_run_installer(self):
        updater=self.manager.updates
        with patch('bridge.features.updates.codex.installed',side_effect=OSError('private path')),patch('bridge.features.updates.codex.subprocess.run') as run:
            updater._check();run.assert_not_called()
        self.assertEqual(updater.status()['state'],'error')
        self.assertNotIn('private path',str(updater.status()))

    def test_windows_store_check_matches_the_installed_product_and_never_installs(self):
        updater=self.manager.updates
        meta={'version':'1.0','build':'1.0.0.0','codexBuildFlavor':'prod','storeProductId':'9PLM9XGG6VKS','packageIdentity':'OpenAI.Fixture'}
        import json
        manifest={'buildVersion':'2.0.0.0','storeProductId':meta['storeProductId'],'packageIdentity':meta['packageIdentity']}
        with patch('bridge.features.updates.codex.sys.platform','win32'),patch('bridge.features.updates.codex.installed',return_value=meta),patch.object(updater,'fetch',return_value=json.dumps(manifest).encode()),patch('bridge.features.updates.codex.os.startfile',create=True) as launch:
            updater._check();self.assertEqual(updater.status()['state'],'available');launch.assert_not_called()
            updater._request(meta);launch.assert_called_once_with('ms-windows-store://pdp/?PRODUCTID=9PLM9XGG6VKS')
            self.assertEqual(updater.status()['state'],'needsDesktop')
        manifest['packageIdentity']='Other.App'
        with patch('bridge.features.updates.codex.sys.platform','win32'),patch('bridge.features.updates.codex.installed',return_value=meta),patch.object(updater,'fetch',return_value=json.dumps(manifest).encode()):
            updater._check();self.assertEqual(updater.status()['state'],'error')

    def test_installation_is_only_verified_by_newer_build_of_same_selected_app(self):
        from pathlib import Path
        import os
        updater = self.manager.updates
        executable = '/fixture/Selected.app/Contents/MacOS/Codex'
        selected = os.path.normcase(str(Path(executable).resolve()))
        meta = {'version':'2.0','build':'2','codexBuildFlavor':'prod',
                'codexSparkleFeedUrl':'https://persistent.oaistatic.com/codex-app-prod/appcast.xml'}
        for current, pending_executable, expected in [('1', selected, False), ('0', selected, False),
                                                       ('2', selected + '-other', False), ('2', selected, True)]:
            updater.pending = {'build':'1','executable':pending_executable}
            updater.value = {'state':'needsDesktop'}
            with self.subTest(current=current, same_app=pending_executable==selected), \
                 patch.object(updater, 'executable', return_value=executable), \
                 patch('bridge.features.updates.codex.installed', return_value={**meta, 'build':current}), \
                 patch('bridge.features.updates.codex.sys.platform','darwin'), \
                 patch.object(updater, 'fetch', return_value=self.feed('')), \
                 patch('bridge.features.updates.codex.subprocess.run') as run:
                updater._check()
                self.assertEqual(updater.status().get('installation', {}).get('state') == 'verified', expected)
                self.assertFalse(updater.status()['canInstall'])
                self.assertEqual(updater.status()['updateMethod'], 'native-handoff')
                run.assert_not_called()
                if expected:
                    self.assertEqual(updater.pending, {})
                    self.assertEqual(updater.status()['installation']['previousBuild'], '1')
                    self.assertEqual(updater.status()['installation']['currentBuild'], '2')

    def test_pending_update_verification_survives_gateway_restart(self):
        from bridge.features.updates.codex import DesktopUpdates
        updater = self.manager.updates
        updater.value = {'state':'available','canRequest':True}
        value = {'requestId':str(uuid.uuid4()),'confirmed':True,'tasksConfirmed':True,'currentBuild':'1'}
        with patch.object(updater, 'executable', return_value='/fixture/Codex'), \
             patch('bridge.features.updates.codex.installed',return_value={'build':'1','bundle':'/fixture.app'}), \
             patch('bridge.features.updates.codex.sys.platform','darwin'), \
             patch('bridge.features.updates.codex.threading.Thread'):
            updater.request(value)
        other = DesktopUpdates(self.manager)
        self.assertEqual(other.pending, updater.pending)
        self.assertEqual(other.pending['build'], '1')
        self.assertFalse(other.status()['canInstall'])
        self.assertNotIn('installation', other.status())

    def test_linux_never_advertises_remote_installer(self):
        updater = self.manager.updates
        with patch('bridge.features.updates.codex.sys.platform','linux'), \
             patch('bridge.features.updates.codex.installed',return_value={'version':'1','build':'1','codexBuildFlavor':'prod'}), \
             patch.object(updater, 'fetch') as fetch, \
             patch('bridge.features.updates.codex.subprocess.run') as run:
            updater._check()
        self.assertEqual(updater.status()['state'], 'unsupported')
        self.assertEqual(updater.status()['updateMethod'], 'unavailable')
        self.assertFalse(updater.status()['canRequest'])
        self.assertFalse(updater.status()['canInstall'])
        fetch.assert_not_called(); run.assert_not_called()


if __name__=='__main__':unittest.main()
