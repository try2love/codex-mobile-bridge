"""Windows-only local RPC holder; official remote business stays in Electron."""
import contextlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import struct
import sys
import threading
import time

from .local_rpc import MAX_FRAME, packet, read_packet, write_packet


def serve(profile, name, parent_pid):
    if os.name != 'nt' or not re.fullmatch('[a-z-]+', name):
        raise ValueError('此本机服务不适用于当前系统')
    import msvcrt
    from bridge.platforms.windows import local_service as native
    for stream in (sys.stdin, sys.stdout):msvcrt.setmode(stream.fileno(), os.O_BINARY)
    profile = Path(profile).resolve(strict=True)
    owner = native.require_parent(parent_pid)
    endpoint = '\\\\.\\pipe\\cmb-rpc-' + name + '-' + secrets.token_hex(16)
    token = secrets.token_hex(32)
    record = profile/'.local-services'/name/'ipc.json'
    listener = native.listener(endpoint)
    closing, slots, lock, output_lock = threading.Event(), threading.BoundedSemaphore(16), threading.Lock(), threading.Lock()
    pending = {}

    def output(value):
        with output_lock:sys.stdout.buffer.write(packet(value));sys.stdout.buffer.flush()

    def handle(conn):
        try:
            with conn:
                native.require_peer(conn, server=True)
                pipe = native.byte_stream(conn);deadline = time.monotonic() + 185
                message = read_packet(lambda size: pipe.recv(size, timeout=deadline-time.monotonic()))
                supplied = message.get('token')
                if not isinstance(supplied, str) or not hmac.compare_digest(supplied, token):return
                if closing.is_set():return
                identifier = secrets.token_hex(16)
                event, reply = threading.Event(), []
                with lock:pending[identifier] = (event, reply)
                try:
                    output({'type': 'request', 'id': identifier, 'request': message.get('request')})
                    if not event.wait(185) or closing.is_set():return
                    write_packet(lambda data: pipe.send(data, timeout=185), reply[0])
                finally:
                    with lock:pending.pop(identifier, None)
        except (OSError, ValueError, EOFError, KeyError, TypeError):
            pass  # Rejected peers never reach the Electron handler or receive internal detail.
        finally:slots.release()

    def accept():
        while not closing.is_set():
            try:conn = listener.accept()
            except (OSError, EOFError):return
            if not slots.acquire(blocking=False):conn.close();continue
            threading.Thread(target=handle, args=(conn,), daemon=True).start()

    def exact(count):
        data = bytearray()
        while len(data) < count:
            block = sys.stdin.buffer.read(count - len(data))
            if not block:raise EOFError()
            data.extend(block)
        return data

    try:
        native.write_record(record, {'endpoint': endpoint, 'token': token, 'owner': owner,
                                     'parentPid': parent_pid, 'profile': str(profile), 'name': name})
        threading.Thread(target=accept, daemon=True).start()
        output({'type': 'ready'})
        while True:
            head = exact(4);size = struct.unpack('!I', head)[0]
            if not 0 < size <= MAX_FRAME:raise ValueError('本机服务父进程回执过大')
            message = json.loads(exact(size))
            if not isinstance(message, dict) or set(message) != {'id', 'reply'}:raise ValueError('本机服务父进程回执无效')
            with lock:
                waiting = pending.get(message['id'])
                if waiting:waiting[1].append(message['reply']);waiting[0].set()
    except EOFError:
        pass  # Closing the parent's anonymous stdin is the normal holder shutdown.
    finally:
        closing.set()
        with lock:
            for event, _ in pending.values():event.set()
        with contextlib.suppress(OSError):listener.close()
        with contextlib.suppress(OSError, ValueError, KeyError):
            if native.read_record(record).get('token') == token:record.unlink()
