"""Standalone stdlib CLI client invoked by Antigravity lifecycle hooks."""
import argparse
import json
from pathlib import Path
import socket
import struct
import sys

CONNECT_TIMEOUT = 0.05
MAX_FRAME_BYTES = 4 * 1024 * 1024


def _default_output(event):
    if event == 'pre_tool_use':
        return {'decision': 'ask'}
    return {}


def _read_stdin_json():
    try:
        raw = sys.stdin.read()
        if not raw or not raw.strip():
            return {}
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _send_and_recv(endpoint, request, read_timeout):
    transport = endpoint.get('transport')
    if transport == 'unix':
        address = endpoint.get('address')
        if not address or not hasattr(socket, 'AF_UNIX'):
            return None
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        target = str(address)
    elif transport == 'tcp':
        host = str(endpoint.get('host') or '127.0.0.1')
        port = int(endpoint.get('port') or 0)
        if host != '127.0.0.1' or not 1024 <= port <= 65535:
            return None
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        target = (host, port)
    else:
        return None

    with sock:
        sock.settimeout(CONNECT_TIMEOUT)
        sock.connect(target)
        sock.settimeout(read_timeout)
        encoded = json.dumps(request, ensure_ascii=False).encode('utf-8')
        sock.sendall(struct.pack('>I', len(encoded)) + encoded)

        header = b''
        while len(header) < 4:
            chunk = sock.recv(4 - len(header))
            if not chunk:
                return None
            header += chunk
        (length,) = struct.unpack('>I', header)
        if length <= 0 or length > MAX_FRAME_BYTES:
            return None
        data = bytearray()
        while len(data) < length:
            chunk = sock.recv(min(65536, length - len(data)))
            if not chunk:
                return None
            data.extend(chunk)
        resp = json.loads(data.decode('utf-8', errors='replace'))
        return resp if isinstance(resp, dict) else None


def main(argv=None):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('event', nargs='?', default='')
    parser.add_argument('--state-dir', dest='state_dir', default='')
    args, _ = parser.parse_known_args(argv)

    event = str(args.event or '').strip()
    fallback = _default_output(event)
    payload = _read_stdin_json()

    if not args.state_dir:
        sys.stdout.write(json.dumps(fallback, ensure_ascii=False) + '\n')
        return 0

    endpoint_file = Path(args.state_dir) / 'hook-endpoint.json'
    try:
        endpoint = json.loads(endpoint_file.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        sys.stdout.write(json.dumps(fallback, ensure_ascii=False) + '\n')
        return 0

    token = endpoint.get('token') if isinstance(endpoint, dict) else None
    if not token:
        sys.stdout.write(json.dumps(fallback, ensure_ascii=False) + '\n')
        return 0

    read_timeout = 288.0 if event == 'pre_tool_use' else 4.0
    try:
        resp = _send_and_recv(
            endpoint,
            {'token': token, 'event': event, 'payload': payload},
            read_timeout=read_timeout,
        )
    except (OSError, ValueError):
        resp = None

    out = resp if isinstance(resp, dict) else fallback
    sys.stdout.write(json.dumps(out, ensure_ascii=False) + '\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
