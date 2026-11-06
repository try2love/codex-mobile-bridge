"""Assemble the cloud-only HTTP listener without sharing direct-login authority."""
import threading

from bridge.api.httpd import GatewayServer


def create_cloud_server(gateway):
    # Internal credentials live only for this relay connection. Connector checks
    # binding authority per request and revokes them on access close/disconnect;
    # an independent browser timeout must not strand an approved Noise channel.
    config = {'auth': {'mode': 'password', 'sessionHours': 0}, 'origins': [], 'localAccess': True}
    # Reuse business services and limits, not the direct listener's auth or pairing.
    server = GatewayServer(('127.0.0.1', 0), gateway.bridge, config, gateway.web_dir)
    try:
        for name in ('transfer_directory', 'mobile_updates', 'notifications', 'desktop_sessions',
                     'desktop_uploads', 'harness', 'ui_language_path', 'slots', 'terminal_sockets'):
            setattr(server, name, getattr(gateway, name, None))
        from bridge.features.accounts.cloud_summary import current_summary, summary_query
        def account_summary(path):
            entry, section, refresh = summary_query('GET', path)
            if not gateway.desktop_sessions:
                raise PermissionError('应用接入不可用')
            gateway.desktop_sessions.require_enabled('codex' if entry == 'desktop' else entry)
            return current_summary(gateway.bridge.account_target(entry).accounts, section, refresh)
        server.cloud_account_summary = account_summary
        origin = 'http://127.0.0.1:' + str(server.server_port)
        server.origins.add(origin)
        server.hosts.add(origin.removeprefix('http://'))
        return server
    except Exception:
        server.server_close()
        raise

class CloudGateway:
    """Only an explicit cloud login owns a connector, listener and relay threads."""

    def __init__(self, directory, gateway):
        self.directory = directory
        self.gateway = gateway
        self.connector = None
        self.closed = False
        self.lock = threading.RLock()

    def reconcile(self):
        # Normal startup, explicit login and logout all use the same gate.
        with self.lock:
            if self.closed:
                return
            configured = False
            if (self.directory / 'cloud-account.json').is_file():
                from bridge.features.network.cloud_account import Controller
                try:
                    configured = bool(Controller(self.directory).config())
                except (OSError, ValueError):
                    # A damaged optional profile must not prevent direct access.
                    configured = False
            if configured and self.connector is None:
                from bridge.features.network.cloud_connector import Connector
                candidate = Connector(self.directory, self.gateway)
                try:
                    candidate.start(lambda: create_cloud_server(self.gateway))
                except ImportError:
                    candidate.close()
                    candidate.status('unavailable', 'cloud_dependencies_missing')
                except (OSError, ValueError):
                    candidate.close()
                    candidate.status('unavailable', 'connection_failed')
                else:
                    self.connector = candidate
            elif not configured and self.connector is not None:
                self.connector.close()
                self.connector = None

    def control(self, value):
        if not isinstance(value, dict):
            raise ValueError('Invalid cloud operation')
        with self.lock:
            if self.closed:
                raise ValueError('Cloud gateway stopped')
            if self.connector is not None:
                controller = self.connector
            else:
                from bridge.features.network.cloud_account import Controller
                controller = Controller(self.directory)
            try:
                return controller.control(value)
            finally:
                if value.get('action') in ('login', 'logout'):
                    self.reconcile()

    def close(self):
        with self.lock:
            self.closed = True
            if self.connector is not None:
                self.connector.close()
                self.connector = None
