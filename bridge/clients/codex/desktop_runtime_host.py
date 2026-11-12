"""Private CLI entry launched by Codex Desktop, not by a background gateway."""
import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import Future
from pathlib import Path

from bridge.clients.codex.desktop_runtime import (
    VERSION, _read, _write, directory, process_identity, server_arguments,
    exclusive_lock, private_directory, socket_path, require_inactive, verified_socket)
from bridge.clients.websocket import WebSocket
from bridge.clients.codex.app_server import AppServerMessageTooLarge as MessageTooLarge


MAX_LINE = 64 * 1024 * 1024


def _forward_input(stream, transport, emit, stop):
    """Reject one invalid/oversized request without disconnecting the App."""
    while not stop.is_set():
        line = stream.readline(MAX_LINE + 2)
        if not line:
            return
        if len(line.rstrip(b'\r\n')) > MAX_LINE:
            while not line.endswith(b'\n'):
                line = stream.readline(65536)
                if not line:
                    break
            # A truncated JSON document has no safely attributable request ID.
            emit({'id': None, 'error': {'code': -32600, 'message': 'Desktop message exceeds the native 64 MiB limit'}})
            continue
        try:
            packet = json.loads(line)
        except (ValueError, UnicodeError):
            emit({'id': None, 'error': {'code': -32700, 'message': 'Invalid Desktop JSON message'}})
            continue
        if not isinstance(packet, dict):
            emit({'id': None, 'error': {'code': -32600, 'message': 'Invalid Desktop request'}})
            continue
        try:
            transport.send(packet)
        except MessageTooLarge:
            if 'id' in packet and 'method' in packet:
                emit({'id': packet['id'], 'error': {'code': -32600, 'message': 'Desktop message exceeds the native 64 MiB limit'}})
            else:
                print('Codex shared runtime: oversized notification or response was not sent', file=sys.stderr)


def _private_socket(config, endpoint, generation, active, record):
    """Verify the native advertisement without changing its link or target."""
    if endpoint != socket_path(config['home'], generation) or _read(active) != record:
        raise ValueError('Native Desktop socket generation changed')
    identity = verified_socket(endpoint)
    if endpoint != socket_path(config['home'], generation) or _read(active) != record:
        raise ValueError('Native Desktop socket generation changed')
    if verified_socket(endpoint) != identity:
        raise ValueError('Native Desktop socket changed during verification')


def run(configuration, arguments):
    config_path = Path(configuration).resolve()
    value = _read(config_path)
    home = Path(value['home']).resolve()
    if value.get('version') != VERSION or config_path != directory(home) / 'settings.json':
        raise ValueError('Invalid shared Desktop runtime configuration')
    cli = value['cli']
    generation = str(uuid.uuid4())
    endpoint = socket_path(home, generation)
    server_args = server_arguments(arguments, endpoint)
    # All unrelated native CLI calls retain their original behavior and IO.
    if server_args is None or Path(os.environ.get('CODEX_HOME', '')).resolve() != home:
        os.execv(cli, [cli, *arguments])
    app = process_identity(os.getppid())
    if app['image'] != value['application']:
        os.execv(cli, [cli, *arguments])
    first = sys.stdin.buffer.readline(MAX_LINE + 1)
    if len(first) > MAX_LINE:
        raise ValueError('Desktop initialization is too large')
    try:
        initialize = json.loads(first)
    except (ValueError, UnicodeError):
        initialize = {}
    primary = (initialize.get('method') == 'initialize' and
               initialize.get('params', {}).get('clientInfo', {}).get('name') == 'Codex Desktop')
    if not primary:
        return _passthrough(cli, arguments, first)
    if os.environ.get('CODEX_SQLITE_HOME') and Path(os.environ['CODEX_SQLITE_HOME']).resolve() != home:
        # A different storage boundary is not an authorized default-home runtime.
        return _passthrough(cli, arguments, first)
    with exclusive_lock(home, 'runtime'):
        private_directory(endpoint.parent)
        return _serve(value, app, server_args, endpoint, initialize, generation)


def _passthrough(cli, arguments, first):
    """Preserve an auxiliary stdio caller after examining its first message."""
    child = subprocess.Popen([cli, *arguments], stdin=subprocess.PIPE)
    stop = threading.Event()
    def input_loop():
        try:
            child.stdin.write(first); child.stdin.flush()
            while data := sys.stdin.buffer.read1(65536):
                child.stdin.write(data); child.stdin.flush()
        except (OSError, ValueError):
            pass
        finally:
            child.stdin.close()
    threading.Thread(target=input_loop, daemon=True).start()
    previous = {sig: signal.signal(sig, lambda *_: stop.set()) for sig in (signal.SIGTERM, signal.SIGINT)}
    def wait():
        child.wait(); stop.set()
    threading.Thread(target=wait, daemon=True).start()
    try:
        stop.wait()
        if child.poll() is None:
            child.terminate()
        return child.wait()
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def _serve(config, app, arguments, endpoint, initialize, generation):
    root = directory(config['home'])
    active = root / 'active.json'
    if active.exists():
        require_inactive(_read(active))
    record = {'version': VERSION, 'ready': False, 'home': config['home'], 'cli': config['cli'],
              'app': app, 'adapter': process_identity(os.getpid()), 'runtime': None,
              'socket': str(endpoint), 'generation': generation}
    # Reserve before spawn, so even an interrupted launch cannot silently create
    # another writer. A ready record is published only after native initialize.
    _write(active, json.dumps(record))
    native = None
    proxy = None
    stop = threading.Event()
    errors = []
    output_lock = threading.Lock()
    record_lock = threading.Lock()
    previous = {sig: signal.signal(sig, lambda *_: stop.set()) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        # The native runtime inherits the App's environment and working dir.
        # Only transport changes. Its stderr belongs to the original App.
        native = subprocess.Popen([config['cli'], *arguments], stdin=subprocess.DEVNULL,
                                  stdout=subprocess.DEVNULL)
        record['runtime'] = process_identity(native.pid)
        if record['runtime']['parent'] != os.getpid():
            raise ValueError('Native Desktop child identity changed')
        _write(active, json.dumps(record))
        deadline = time.monotonic() + 25
        while not endpoint.exists():
            if stop.wait(.05) or native.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError('Native Desktop runtime did not become ready')
        _private_socket(config, endpoint, generation, active, record)
        proxy = subprocess.Popen([config['cli'], 'app-server', 'proxy', '--sock', str(endpoint)],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0)
        handshake = Future()
        def open_transport():
            try:
                handshake.set_result(WebSocket(proxy.stdout, proxy.stdin,
                                               max_message_bytes=MAX_LINE, oversized=MessageTooLarge))
            except Exception as exc:
                handshake.set_exception(exc)
        threading.Thread(target=open_transport, daemon=True).start()
        transport = handshake.result(timeout=25)
        record['runtime'] = process_identity(native.pid)
        _write(active, json.dumps(record))
        # Bundled `bin/codex` may be a native launch script that execs CodexCLI.app.
        # Pin the actual child generation, not the spelling of the launch command.
        if process_identity(app['pid']) != app or record['runtime']['parent'] != os.getpid():
            raise ValueError('Native Desktop process identity changed')
        def emit(packet):
            with output_lock:
                sys.stdout.write(json.dumps(packet, ensure_ascii=False, separators=(',', ':')) + '\n')
                sys.stdout.flush()
        def output_loop():
            try:
                while not stop.is_set():
                    packet = transport.receive()
                    if packet.get('id') == initialize.get('id') and 'result' in packet:
                        if Path(packet['result']['codexHome']).resolve() != Path(config['home']):
                            raise ValueError('Native Desktop initialized a different home')
                        for kind in ('app', 'adapter', 'runtime'):
                            if process_identity(record[kind]['pid']) != record[kind]:
                                raise ValueError('Native Desktop owner changed during initialization')
                        with record_lock:
                            if stop.is_set():
                                return
                            record['ready'] = True
                            _write(active, json.dumps(record))
                    emit(packet)
            except (OSError, EOFError, ValueError, KeyError, RuntimeError) as exc:
                if not stop.is_set():
                    errors.append(exc)
            finally:
                stop.set()
        def input_loop():
            try:
                transport.send(initialize)
                _forward_input(sys.stdin.buffer, transport, emit, stop)
            except (OSError, ValueError, RuntimeError) as exc:
                if not stop.is_set():
                    errors.append(exc)
            finally:
                stop.set()
        threading.Thread(target=output_loop, daemon=True).start()
        threading.Thread(target=input_loop, daemon=True).start()
        stop.wait()  # No polling or listeners are created by a disabled gateway.
    finally:
        stop.set()
        if proxy is not None:
            if proxy.poll() is None:
                proxy.terminate()  # This byte proxy has no model/terminal ownership.
            try:
                proxy.wait(timeout=5)
            except subprocess.TimeoutExpired:
                errors.append(RuntimeError('Native byte proxy has not exited'))
        if native is not None and native.poll() is None:
            native.terminate()  # App EOF/exit, never a Bridge disconnect.
        if native is not None:
            try:
                native.wait(timeout=25)
            except subprocess.TimeoutExpired:
                # Retain the starting record too, even if initialize never finished.
                errors.append(RuntimeError('Native runtime has not exited; not force terminated'))
        exited = native is None or native.poll() is not None
        with record_lock:
            if exited and active.exists() and _read(active) == record:
                active.unlink()
        if exited:
            endpoint.unlink(missing_ok=True)
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    if errors:
        print('Codex shared runtime: ' + str(errors[0]), file=sys.stderr)
        return 1
    return native.returncode or 0
