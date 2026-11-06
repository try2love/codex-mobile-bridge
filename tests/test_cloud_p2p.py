import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from bridge.features.network.cloud_connector import Connector
from bridge.features.network.cloud_p2p import P2P
from bridge.features.network.cloud_account import CloudError


class P2PLifetime(unittest.IsolatedAsyncioTestCase):
    async def test_local_binding_revocation_closes_only_target_and_releases_helper(self):
        with tempfile.TemporaryDirectory() as directory:
            connector = Connector(Path(directory), SimpleNamespace())
            try:
                first = asyncio.create_task(asyncio.Event().wait())
                other = asyncio.create_task(asyncio.Event().wait())
                connector.channels = {
                    'first': SimpleNamespace(binding='first', task=first, p2p=True),
                    'other': SimpleNamespace(binding='other', task=other, p2p=True),
                }
                connector.p2p.block = AsyncMock()
                await connector.block_binding('first')
                self.assertTrue(first.cancelled())
                self.assertFalse(other.done())
                connector.p2p.block.assert_awaited_once_with('first')
                other.cancel()
                await asyncio.gather(other, return_exceptions=True)
            finally:
                connector.notifications.close()

    async def test_helper_shutdown_closes_stdin_then_terminates_if_it_does_not_exit(self):
        process = SimpleNamespace(stdin=Mock(), wait=AsyncMock(return_value=0), kill=Mock())
        await P2P.stop_process(process)
        process.stdin.close.assert_called_once()
        process.kill.assert_not_called()

    async def test_revocation_delivers_close_before_destroying_overlay(self):
        with tempfile.TemporaryDirectory() as directory:
            connector = Connector(Path(directory), SimpleNamespace())
            order = []
            try:
                socket = SimpleNamespace(close=AsyncMock(side_effect=lambda **kwargs: order.append('close')))
                connector.channels = {'channel': SimpleNamespace(binding='binding', task=None, p2p=True, socket=socket)}
                connector.p2p.block = AsyncMock(side_effect=lambda binding: order.append('helper'))
                await connector.block_binding('binding')
                self.assertEqual(order, ['close', 'helper'])
            finally:
                connector.notifications.close()

    async def test_new_connection_never_uses_cached_offer_without_server_validation(self):
        from aiohttp import web
        config = {'origin': 'https://server.test', 'account_id': 'account', 'gateway_id': 'desktop'}
        owner = SimpleNamespace(config=config, channels={}, access_generations={},
            pin=Mock(return_value={'mobile_public_key': 'unused'}),
            accounts=SimpleNamespace(device_request=Mock(side_effect=OSError('offline')), config=Mock(return_value=config)),
            authority_context=lambda: (config, 'token', 0),
            account_identity=lambda c: ('server', 'account', 'desktop'))
        p2p = P2P(owner)
        p2p.offers['binding'] = {'public': {'offer_id': 'offer'}}
        request = SimpleNamespace(headers={'X-Bridge-Binding': 'binding', 'X-Bridge-P2P': 'offer'})
        with self.assertRaises(web.HTTPServiceUnavailable):
            await p2p.accept(request)
        owner.accounts.device_request.assert_called_once()

    async def test_delayed_denial_does_not_close_renewed_authority(self):
        for changed in ('token', 'policy', 'account', None):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as directory:
                connector = Connector(Path(directory), SimpleNamespace())
                connector.config = {'origin': 'https://server.test', 'account_id': 'account', 'gateway_id': 'desktop'}
                credentials = {'device_token': 'old'}
                connector.accounts = SimpleNamespace(config=lambda: connector.config, secret=lambda config: credentials)
                connector.stop_authority = AsyncMock()
                def denied():
                    if changed == 'token':
                        credentials['device_token'] = 'renewed'
                    elif changed == 'policy':
                        connector.policy_generation += 1
                    elif changed == 'account':
                        connector.config = dict(connector.config, account_id='other')
                    raise CloudError('access_denied', 403)
                connector.drain_notifications = denied
                try:
                    await connector.recheck_access()
                    self.assertEqual(connector.stop_authority.await_count, int(changed is None))
                finally:
                    connector.notifications.close()

    async def test_delayed_p2p_grant_does_not_use_renewed_credentials(self):
        from aiohttp import web
        with tempfile.TemporaryDirectory() as directory:
            owner = Connector(Path(directory), SimpleNamespace())
            owner.config = {'origin': 'https://server.test', 'account_id': 'account', 'gateway_id': 'desktop'}
            credentials = {'device_token': 'old'}
            def grant(*args):
                credentials['device_token'] = 'renewed'
                return {'origin': owner.config['origin'], 'account_id': 'account', 'device_id': 'desktop',
                        'binding': {'access_revision': 1}}
            owner.accounts = SimpleNamespace(config=lambda: owner.config, secret=lambda config: credentials,
                                             device_request=grant)
            owner.pin = Mock(return_value={'mobile_public_key': 'unused'})
            owner.p2p.offers['binding'] = {'public': {'offer_id': 'offer', 'access_revision': 1}}
            request = SimpleNamespace(headers={'X-Bridge-Binding': 'binding', 'X-Bridge-P2P': 'offer'})
            try:
                with self.assertRaises(web.HTTPForbidden):
                    await owner.p2p.accept(request)
                self.assertEqual(owner.channels, {})
            finally:
                owner.notifications.close()

    async def test_queued_denial_does_not_close_a_reauthorized_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            owner = Connector(Path(directory), SimpleNamespace())
            owner.config = {'origin': 'https://server.test', 'account_id': 'account', 'gateway_id': 'desktop'}
            owner.accounts = SimpleNamespace(config=lambda: owner.config, secret=lambda config: {'device_token': 'token'})
            owner.block_binding = AsyncMock()
            context = owner.authority_context()
            identity = (owner.account_identity(owner.config), 'binding')
            owner.access_generations[identity] = 2
            try:
                await owner.block_if_current('binding', context, 1)
                owner.block_binding.assert_not_awaited()
                await owner.block_if_current('binding', context, 2)
                owner.block_binding.assert_awaited_once_with('binding')
            finally:
                owner.notifications.close()
