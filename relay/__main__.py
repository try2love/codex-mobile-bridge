"""Headless relay with terminal administration and an optional web admin login."""
import argparse
import json
import os
from .registry import Registry


def main():
    parser = argparse.ArgumentParser(description='Codex Bridge shared relay (headless)')
    parser.add_argument('--data-dir', required=True)
    commands = parser.add_subparsers(dest='command', required=True)
    invite = commands.add_parser('invite')
    invite.add_argument('--owner', required=True)
    invite.add_argument('--hours', type=int, default=24)
    commands.add_parser('devices')
    commands.add_parser('admin-token', help='Generate/rotate the web admin key and invalidate admin sessions')
    revoke = commands.add_parser('revoke')
    revoke.add_argument('device_id')
    serve = commands.add_parser('serve')
    serve.add_argument('--origin', required=True)
    serve.add_argument('--host', default='127.0.0.1')
    serve.add_argument('--port', type=int, default=18790)
    args = parser.parse_args()
    os.umask(0o077)
    if args.command == 'serve':
        from aiohttp import web
        from .server import create_app
        print('Web admin: ' + args.origin.rstrip('/') + '/relay/admin/', flush=True)
        web.run_app(create_app(args.data_dir, args.origin), host=args.host, port=args.port, access_log=None)
        return
    registry = Registry(args.data_dir)
    try:
        if args.command == 'invite':
            result = registry.invite(args.owner, args.hours)
        elif args.command == 'devices':
            result = registry.devices()
        elif args.command == 'admin-token':
            result = {'adminToken': registry.rotate_admin_token()}
        else:
            registry.revoke(args.device_id)
            result = {'revoked': args.device_id}
        print(json.dumps(result, ensure_ascii=False))
    finally:
        registry.close()


if __name__ == '__main__':
    main()
