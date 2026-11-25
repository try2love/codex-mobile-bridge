"""Assemble the cloud-only HTTP listener without sharing direct-login authority."""
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
        origin = 'http://127.0.0.1:' + str(server.server_port)
        server.origins.add(origin)
        server.hosts.add(origin.removeprefix('http://'))
        return server
    except Exception:
        server.server_close()
        raise
