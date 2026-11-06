import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from bridge.app.cloud import CloudGateway
from bridge.features.network.cloud_connector import Connector


class CloudGatewayTests(unittest.TestCase):
    def test_disabled_and_status_never_allocate_a_listener_then_logout_releases_it(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp') as directory:
            root=Path(directory)
            gateway=CloudGateway(root,Mock())
            account=root/'cloud-account.json'
            with patch('bridge.app.cloud.create_cloud_server') as listen, \
                    patch('bridge.features.network.cloud_account.Controller') as controller, \
                    patch('bridge.features.network.cloud_connector.Connector',autospec=Connector) as make:
                gateway.reconcile()
                gateway.control({'action':'status'})
                make.assert_not_called()
                listen.assert_not_called()
                controller.return_value.config.return_value={'origin':'https://example.test'}
                controller.return_value.control.side_effect=lambda _: account.touch()
                make.return_value.start.side_effect=lambda factory:factory()
                gateway.control({'action':'login'})
                gateway.reconcile()
                make.assert_called_once()
                listen.assert_called_once()
                make.return_value.control.side_effect=lambda _: account.unlink()
                gateway.control({'action':'logout'})
                make.return_value.close.assert_called_once()
                self.assertIsNone(gateway.connector)
                gateway.close()

    def test_damaged_cloud_profile_does_not_start_a_service_or_break_the_gateway(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp') as directory:
            root=Path(directory)
            (root/'cloud-account.json').write_text('{"unexpected":"format"}')
            gateway=CloudGateway(root,Mock())
            with patch('bridge.app.cloud.create_cloud_server') as listener:
                gateway.reconcile()
                listener.assert_not_called()
            self.assertIsNone(gateway.connector)
