"""Desktop-owned Windows stdio runtime with a private metadata-only endpoint."""
import contextlib
import hmac
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
import uuid
from types import SimpleNamespace

from bridge.clients.local_rpc import LocalService, LocalRequestNotSent, read_packet, write_packet
from .app_server import (AppServerError, AppServerUnavailable, AppServerUnknownResult,
                         AppServerRequestNotSent, response_result)
from .desktop_runtime import (VERSION, _read, _write, directory, exclusive_lock,
                              process_identity, require_inactive, server_arguments)
from .desktop_runtime_host import _forward_input
from .desktop_stdio import DesktopStdio, MAX_LINE, METADATA_METHODS


NAME = 'codex-desktop'


def _verify(record):
    if _read(directory(record['home']) / 'active.json') != record:
        raise ValueError('Codex 原生连接记录已变化')
    for key in ('app', 'adapter', 'runtime'):
        if process_identity(record[key]['pid']) != record[key]:
            raise ValueError('Codex 原生连接进程已变化')


class MetadataClient:
    def __init__(self, profile, record):
        self.service = LocalService(profile, NAME)
        self.record = record
        self.closed = False

    def connect(self):
        try:
            value = self.service._exchange({'command': 'status'}, timeout=5)
            if (value.get('generation') != self.record['generation'] or value.get('ready') is not True
                    or value.get('home') != self.record['home']):
                raise ValueError('原生连接身份不匹配')
            _verify(self.record)
        except (OSError, ValueError, EOFError, KeyError, TypeError) as exc:
            raise AppServerUnavailable('Codex 原生连接尚未就绪，请重新初始化连接') from exc

    def request(self, method, params, timeout=90):
        if self.closed or method not in METADATA_METHODS or not isinstance(params, dict):
            raise AppServerRequestNotSent('原生元数据连接不可用')
        try:
            _verify(self.record)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise AppServerRequestNotSent('Codex 原生连接已变化') from exc
        try:
            response = self.service._exchange({'command': 'call', 'generation': self.record['generation'],
                                               'method': method, 'params': params}, timeout=timeout)
        except LocalRequestNotSent as exc:
            raise AppServerRequestNotSent(str(exc)) from exc
        except (OSError, ValueError, EOFError, KeyError, TypeError) as exc:
            raise AppServerUnknownResult(str(exc) or '原生操作结果尚未确认，不会重复提交') from exc
        return response_result(method, params, response)

    def close(self):
        self.closed = True  # The App, not this Bridge connection, owns native stdin.


def run(configuration, arguments):
    from bridge.platforms.windows import local_service as native
    import msvcrt
    for stream in (sys.stdin, sys.stdout):
        msvcrt.setmode(stream.fileno(), os.O_BINARY)
    path = Path(configuration).resolve()
    config = _read(path)
    home = Path(config['home']).resolve()
    if (config.get('version') != VERSION or path != directory(home) / 'settings.json'
            or Path(config['launcher']).resolve() != Path(sys.executable).resolve()):
        raise ValueError('原生共享连接配置不匹配')
    env = dict(os.environ)
    env.pop('CMB_CODEX_DESKTOP_CONFIGURATION', None)
    # Desktop's override bootstrap removes this variable. Preserve the exact
    # registered package identity instead of weakening its native sandbox.
    if config.get('sandboxFamily'):
        env['CODEX_WINDOWS_SANDBOX_PACKAGE_FAMILY'] = config['sandboxFamily']
    cli = config['cli']
    if server_arguments(arguments, Path('unused.sock')) is None:
        # Windows CRT process overlay can crash before forwarding startup output.
        return subprocess.call([cli, *arguments], env=env, stdin=sys.stdin, stdout=sys.stdout,
                               stderr=sys.stderr, creationflags=subprocess.CREATE_NO_WINDOW)
    app = process_identity(os.getppid())
    if (app['image'] != config['application'] or Path(env.get('CODEX_HOME', '')).resolve() != home
            or Path(env.get('CODEX_SQLITE_HOME') or home).resolve() != home):
        raise ValueError('原生共享连接与当前 App 或配置目录不匹配')
    first = sys.stdin.buffer.readline(MAX_LINE + 2)
    if len(first) > MAX_LINE + 1:
        raise ValueError('原生初始化消息过大')
    try:
        initialize = json.loads(first)
    except (ValueError, UnicodeError):
        initialize = None
    if not (isinstance(initialize, dict) and initialize.get('method') == 'initialize'
            and initialize.get('params', {}).get('clientInfo', {}).get('name') == 'Codex Desktop'):
        # This launcher can also be used by an auxiliary native CLI caller.
        child = subprocess.Popen([cli, *arguments], env=env, stdin=subprocess.PIPE,
                                 stdout=sys.stdout, stderr=sys.stderr, bufsize=0,
                                 creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            child.stdin.write(first)
            for line in sys.stdin.buffer:
                child.stdin.write(line)
        finally:
            child.stdin.close()
        return child.wait()
    with exclusive_lock(home, 'runtime'):
        return _serve(config, app, arguments, initialize, env, native)


def _serve(config, app, arguments, initialize, env, native):
    root = directory(config['home'])
    active = root / 'active.json'
    if active.exists():
        require_inactive(_read(active))
    generation = str(uuid.uuid4())
    record = {'version': VERSION, 'ready': False, 'home': config['home'], 'cli': config['cli'],
              'app': app, 'adapter': process_identity(os.getpid()), 'runtime': None, 'generation': generation}
    _write(active, json.dumps(record))
    child = None
    listener = None
    rpc_record = root / '.local-services' / NAME / 'ipc.json'
    token = secrets.token_hex(32)
    stopped = threading.Event()
    output_lock = threading.Lock()
    slots = threading.BoundedSemaphore(8)

    def emit(message):
        with output_lock:
            sys.stdout.buffer.write(json.dumps(message, ensure_ascii=False).encode('utf-8') + b'\n')
            sys.stdout.buffer.flush()

    def write(data):
        offset = 0
        while offset < len(data):
            count = child.stdin.write(data[offset:])
            if not count:
                raise EOFError('原生桌面连接已关闭')
            offset += count

    def initialized(result):
        if Path(result.get('codexHome', '')).resolve() != Path(config['home']).resolve():
            raise ValueError('原生运行时使用了其他配置目录')
        _verify(record)
        record['ready'] = True
        _write(active, json.dumps(record))

    router = DesktopStdio(write, emit, initialized)

    def handle(connection):
        try:
            with connection:
                native.require_peer(connection, server=True)
                pipe = native.byte_stream(connection)
                deadline = time.monotonic() + 95
                message = read_packet(lambda size: pipe.recv(size, timeout=deadline-time.monotonic()))
                supplied = message.get('token')
                if not isinstance(supplied, str) or not hmac.compare_digest(supplied, token):
                    return
                dispatched = False
                try:
                    _verify(record)
                    request = message.get('request')
                    if stopped.is_set() or not isinstance(request, dict):
                        raise ValueError('原生桌面连接已关闭')
                    if request.get('command') == 'status':
                        value = {'generation': generation, 'home': config['home'], 'ready': router.ready}
                    elif request.get('command') == 'call' and request.get('generation') == generation:
                        dispatched = True
                        value = router.request_packet(request.get('method'), request.get('params'), timeout=max(.01, deadline-time.monotonic()))
                    else:
                        raise ValueError('原生元数据请求无效')
                    response = {'ok': True, 'value': value}
                except (OSError, ValueError, AppServerError, EOFError) as exc:
                    response = {'ok': False, 'error': str(exc), 'notSent': not dispatched or isinstance(exc, AppServerRequestNotSent)}
                write_packet(lambda data: pipe.send(data, timeout=5), response)
        except (OSError, ValueError, EOFError, KeyError, TypeError):
            pass
        finally:
            slots.release()

    def accept():
        while not stopped.is_set():
            try:
                connection = listener.accept()
            except (OSError, EOFError):
                return
            if not slots.acquire(blocking=False):
                connection.close()
                continue
            threading.Thread(target=handle, args=(connection,), daemon=True).start()

    def output():
        try:
            while not stopped.is_set():
                line = child.stdout.readline(MAX_LINE + 2)
                if not line:
                    return
                if len(line.rstrip(b'\r\n')) > MAX_LINE:
                    raise ValueError('原生回执超过消息大小限制')
                message = json.loads(line)
                if not isinstance(message, dict):
                    raise ValueError('原生回执格式无效')
                router.from_native(message)
        except (OSError, ValueError, AppServerError):
            pass
        finally:
            stopped.set()

    def input():
        try:
            router.from_app(initialize)
            _forward_input(sys.stdin.buffer, SimpleNamespace(send=router.from_app), emit, stopped)
        except (OSError, ValueError, AppServerError):
            pass
        finally:
            stopped.set()

    try:
        # No UDS daemon: the wrapper is the only holder of native stdin. Even
        # Desktop's Windows TerminateProcess(wrapper) closes this write handle,
        # causing native connection cleanup without a force-kill of the runtime.
        child = subprocess.Popen([config['cli'], *arguments], env=env, stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, bufsize=0, close_fds=True,
                                 creationflags=subprocess.CREATE_NO_WINDOW)
        record['runtime'] = process_identity(child.pid)
        if record['runtime']['parent'] != os.getpid() or record['runtime']['image'] != os.path.normcase(config['cli']):
            raise ValueError('原生运行时子进程身份不匹配')
        _write(active, json.dumps(record))
        endpoint = '\\\\.\\pipe\\cmb-rpc-' + NAME + '-' + secrets.token_hex(16)
        listener = native.listener(endpoint)
        native.write_record(rpc_record, {'endpoint': endpoint, 'token': token, 'owner': record['adapter'],
                                         'profile': str(root.resolve()), 'name': NAME})
        threading.Thread(target=accept, daemon=True).start()
        threading.Thread(target=output, daemon=True).start()
        threading.Thread(target=input, daemon=True).start()
        stopped.wait()
    finally:
        stopped.set()
        router.close()
        if listener is not None:
            with contextlib.suppress(OSError): listener.close()
        with contextlib.suppress(OSError, ValueError, KeyError):
            if native.read_record(rpc_record).get('token') == token: rpc_record.unlink()
        if child is not None:
            with contextlib.suppress(OSError): child.stdin.close()
            try:
                child.wait(timeout=25)
            except subprocess.TimeoutExpired:
                print('原生运行时仍在正常退出，保留连接记录供下次核验', file=sys.stderr)
                return 1
        if child is not None and active.exists() and _read(active) == record:
            active.unlink()
    return child.returncode if child is not None else 1
