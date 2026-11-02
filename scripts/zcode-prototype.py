#!/usr/bin/env python3
"""Manual protocol harness; deliberately absent from public routes and startup discovery."""
import argparse
import json
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bridge.app.lifecycle import private_json
from bridge.clients.manager import DesktopSessions
from bridge.clients.zcode.adapter import ZCodePrototype
from bridge.clients.zcode.installation import installation, listener_identity


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', required=True, type=Path, help='Private .tmp directory containing binding.json')
    parser.add_argument('action', choices=['attach', 'detach', 'history', 'detail', 'receipt', 'send', 'stop', 'respond'])
    args = parser.parse_args()
    config_path = args.state / 'binding.json'
    config = json.loads(config_path.read_text())
    if args.action == 'attach':
        app = installation(config['application'])
        if not app or app['version'] != '3.17.1':
            raise ValueError('原型仅接受 ZCode 3.17.1')
        config['process'] = listener_identity(config['port'], app['executable'])
    adapter = ZCodePrototype(args.state, config)
    body = json.load(sys.stdin) if args.action in ('send', 'stop', 'respond', 'receipt') else {}
    if args.action in ('send', 'stop', 'respond', 'detail'):
        # The harness supplies one opt-in adapter to the real shared manager. It does
        # not duplicate receipt claiming, per-provider locks or concurrent read coalescing.
        manager = DesktopSessions(args.state)
        try:
            manager.adapters['zcode'] = adapter
            manager.config.setdefault('enabled', {})['zcode'] = True
            manager.locks['zcode'] = threading.RLock()
            for mapping in (manager.read_epochs, manager.read_changes, manager.binding_epochs, manager.binding_changes):
                mapping['zcode'] = 0
            # Avoid unrelated model/account catalog setup in this protocol-only slice.
            result = (manager._read('zcode', 'detail', adapter.sid) if args.action == 'detail'
                      else manager.call('zcode', args.action, adapter.sid, body))
        finally:
            manager.close()
    else:
        result = adapter.call(args.action, adapter.sid, body)
    if args.action == 'attach':
        config['binding'] = result['binding']
        private_json(config_path, config)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
