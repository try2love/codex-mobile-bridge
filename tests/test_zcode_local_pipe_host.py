"""A verified caller, never an arbitrary local desktop process, holds the App pipe."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from bridge.clients.zcode import local_pipe_host as host


class PrivateCallerTests(unittest.TestCase):
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
