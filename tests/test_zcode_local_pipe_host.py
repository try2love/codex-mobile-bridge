"""A verified caller, never an arbitrary local desktop process, holds the App pipe."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from bridge.clients.zcode import local_pipe_host as host


class PrivateCallerTests(unittest.TestCase):
    def test_windows_holder_keeps_verified_application_url_case(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory).resolve()
            app = profile/'ZCode.exe'
            app.touch()
            stream, listener, connection = Mock(), Mock(), Mock()
            connection.__enter__ = Mock(return_value=connection)
            connection.__exit__ = Mock(return_value=False)
            listener.accept.side_effect = [connection, EOFError()]
            native = Mock()
            native.local_listener.return_value = listener
            native.launch.return_value = [7, stream]
            native.identity.return_value = {'pid': 7, 'image': str(app).lower()}
            transports = []

            def transport(*args, **kwargs):
                value = original(*args, **kwargs)
                transports.append(value)
                return value

            def start_thread(*, target, **kwargs):
                def run():
                    try:
                        target()
                    except EOFError:
                        pass
                return Mock(start=run)

            original = host.PipeTransport
            with patch.object(host.sys, 'platform', 'win32'), \
                    patch.object(host.os.path, 'normcase', side_effect=lambda value: str(value).lower()), \
                    patch.object(host, 'platform_pipe', return_value=native), \
                    patch.object(host, 'require_peer'), patch.object(host, 'receive', return_value={'command': 'initialize'}), \
                    patch.object(host, 'send'), patch.object(host.threading, 'Thread', side_effect=start_thread), \
                    patch.object(host, 'PipeTransport', transport):
                host.serve(profile, app)
                rpc = transports[0]._rpc = Mock(side_effect=[
                    {'targetInfos': [{'type': 'page', 'targetId': 'native',
                                      'url': (app.parent/'resources/app.asar/out/renderer/index.html').as_uri()}]},
                    {'sessionId': 'visit'}, {'result': {'value': {'ready': True}}}, {},
                ])
                self.assertEqual(transports[0]._with_window({'action': 'application'}, 100), {'ready': True})
                self.assertEqual(rpc.call_args_list[1].args[0], 'Target.attachToTarget')

    def test_current_gateway_and_exact_role_both_required(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory).resolve()
            app = profile/'ZCode'
            (profile/'gateway-control.json').write_text(json.dumps({'pid': 81}))
            native = Mock()
            native.peer_pid.return_value = 81
            native.identity.side_effect = lambda pid: {'image': 'gateway-image' if pid in (81, os.getpid()) else 'other'}
            prefix = host.gateway_command()[1:]
            argv = ['executable', *prefix, 'serve', '--data-dir', str(profile)]
            with patch.object(host.sys, 'platform', 'win32'), patch.object(host, 'platform_pipe', return_value=native):
                native.process_args.return_value = argv + ['--connection-secrets-stdin']
                host.require_peer(None, profile, app, server=True)
                native.process_args.return_value = argv + ['--other']
                with self.assertRaises(PermissionError):
                    host.require_peer(None, profile, app, server=True)
                native.process_args.return_value = argv
                native.peer_pid.return_value = 82
                with self.assertRaises(PermissionError):
                    host.require_peer(None, profile, app, server=True)
                native.peer_pid.return_value = 81
                native.process_args.return_value = ['exe', *prefix, *host.holder_args(profile, app)]
                with self.assertRaises(PermissionError):
                    host.require_peer(None, profile, app, server=True)
                host.require_peer(None, profile, app, server=False)
                with self.assertRaises(PermissionError):
                    host.require_peer(None, profile, app.parent/'other', server=False)

    def test_initialization_never_takes_over_on_identity_failure(self):
        client = host.LocalPipeClient('.', 'original')
        with patch.object(client, '_request', side_effect=PermissionError('wrong peer')), patch.object(host.subprocess, 'Popen') as launch:
            with self.assertRaises(PermissionError):
                client.start()
            launch.assert_not_called()
