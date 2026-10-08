#!/usr/bin/env python3
"""Private stdio interface used by the desktop application."""
import argparse
import json
import sys
if len(sys.argv) == 3 and sys.argv[1] == '--terminal-child':
    if sys.platform == 'win32':
        from bridge.windows_terminal import child_main
    else:
        from bridge.pty_terminal import child_main
    child_main(sys.argv[2])

from bridge.desktop import Desktop


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stdin.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['management-stream', 'shared-relay', 'read-credentials', 'server-setup', 'connection-credentials', 'snapshot-stream', 'snapshot', 'save', 'start', 'stop', 'logs', 'test-notification', 'deployment', 'export-deployment', 'check-entry', 'devices', 'pairing', 'account', 'accounts', 'harness', 'desktop-sessions', 'notification-watches', 'serve', 'update-prepare', 'update-apply'])
    parser.add_argument('--data-dir', required=True)
    parser.add_argument('--plan')
    parser.add_argument('--connection-secrets-stdin', action='store_true')
    args = parser.parse_args()
    desktop = Desktop(args.data_dir)
    if args.action == 'management-stream':
        # This persistent private channel keeps enrollment and discovery alive
        # while the public gateway is stopped. No network listener is opened.
        try:
            for line in sys.stdin:
                try:
                    value = json.loads(line)
                    action = value.get('action')
                    if action not in ('accounts', 'account', 'desktop-sessions', 'start', 'stop', 'save'):
                        raise ValueError('不支持的本机管理操作')
                    method = getattr(desktop, action.replace('-', '_'))
                    result = method() if action == 'stop' else method(value.get('payload') or {})
                    response = {'ok': True, 'result': result}
                except Exception as exc:
                    response = {'ok': False, 'error': str(exc), 'validation': getattr(exc, 'validation', None)}
                print(json.dumps(response, ensure_ascii=False), flush=True)
        finally:
            desktop.close_local()
        return
    if args.action == 'snapshot-stream':
        # Private stdio only; no new network or management endpoint.
        for line in sys.stdin:
            try:
                if json.loads(line) != {'action': 'snapshot'}:
                    raise ValueError('不支持的状态查询')
                result = {'ok': True, 'result': desktop.snapshot()}
            except Exception as exc:
                result = {'ok': False, 'error': str(exc), 'validation': getattr(exc, 'validation', None)}
            print(json.dumps(result, ensure_ascii=False), flush=True)
        return
    if args.action == 'serve':
        import run
        sys.argv = [sys.argv[0], *desktop.argv()]
        run.main(connections=desktop.preferences()['connections'], connection_secrets=json.loads(sys.stdin.read(1000000) or '{}') if args.connection_secrets_stdin else {})
        return
    try:
        if args.action == 'update-prepare':
            from bridge.updater import prepare
            result = prepare(args.data_dir, json.loads(sys.stdin.read(100000) or '{}'))
        elif args.action == 'update-apply':
            from bridge.updater import apply
            result = apply(args.plan)
        elif args.action == 'test-notification':
            result = desktop.test_notification(json.loads(sys.stdin.read(100000) or '{}'))
        elif args.action in ('start', 'shared-relay', 'read-credentials', 'server-setup', 'connection-credentials', 'save', 'deployment', 'export-deployment', 'check-entry', 'devices', 'pairing', 'account', 'accounts', 'harness', 'desktop-sessions', 'notification-watches'):
            result = getattr(desktop, args.action.replace('-', '_'))(json.loads(sys.stdin.read(100000) or '{}'))
        else:
            method = getattr(desktop, args.action.replace('-', '_'))
            result = method()
        print(json.dumps({'ok': True, 'result': result}, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({'ok': False, 'error': str(exc), 'validation': getattr(exc, 'validation', None)}, ensure_ascii=False))
        sys.exit(1)


if __name__ == '__main__':
    main()
