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


if __name__=='__main__':unittest.main()
