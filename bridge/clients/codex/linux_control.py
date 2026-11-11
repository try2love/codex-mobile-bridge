"""Run the same target Linux lifecycle worker through an SSH or WSL reader."""
import copy
import json
import threading
from contextlib import contextmanager

from bridge.clients.codex.daemon_network import daemon_version, graceful_restart_supported
from bridge.clients.codex.release import ReleaseNotSent
from bridge.clients.codex.ipc import IPCError
from bridge.resources import source_text


@contextmanager
def shared_cli_probe(stream):
    from bridge.clients.codex.app_server import CodexAppServer, AppServerUnavailable, AppServerUnsupported, AppServerError
    from bridge.clients.codex.shared_server import SharedServer
    from bridge.clients.codex.ipc import IPCError
    server = SharedServer(stream)
    class Probe:
        loaded_threads = CodexAppServer.loaded_threads

        def connect(self):
            return server.server_info

        def request(self, method, params, **_kwargs):
            try:
                return server.request(method, params)
            except IPCError as error:
                rpc = getattr(error, 'rpc_error', None)
                if rpc:
                    failure = (AppServerUnsupported if rpc.get('code') == -32601 else AppServerError)('目标 CLI 拒绝此只读检查')
                    failure.rpc_error = rpc
                    raise failure from error
                raise AppServerUnavailable('目标 CLI 只读检查暂不可用') from error
    try:
        server.start()
        yield Probe()
    finally:
        server.close()


_INSPECT = '''
root, _ = control._home(request['home'] or os.environ.get('CODEX_HOME') or str(Path.home()/'.codex'))
record, _ = control._native_record(root)
pid = record.get('pid')
if type(pid) is not int or pid <= 0:
    raise control.ControlError('Native PID record is invalid')
executable = str((Path('/proc')/str(pid)/'exe').resolve(strict=True))
endpoint = str(root/'app-server-control/app-server-control.sock')
before = control.inspect(root, executable, endpoint, None)
generation = before['generation']
info = None
try:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
        peer.settimeout(3)
        peer.connect(generation['socket'])
        if struct.unpack('3i', peer.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))[:2] != (pid,generation['uid']):
            raise control.ControlError('Native initialize peer changed')
        with peer.makefile('rwb',buffering=0) as stream:
            websocket = WebSocket(stream,stream,error=OSError,unavailable=OSError,unsupported=OSError)
            websocket.send({'id':1,'method':'initialize','params':{
                'clientInfo':{'name':'codex-mobile-bridge-target-check','version':'1'},
                'capabilities':{'experimentalApi':True}}})
            for _ in range(32):
                reply = websocket.receive()
                if reply.get('id') == 1:
                    if 'error' in reply:
                        raise OSError('Native initialize unavailable')
                    value = reply['result']
                    info = {key:value.get(key) for key in ('codexHome','userAgent')}
                    websocket.send({'method':'initialized','params':{}})
                    break
except control.ControlError:
    raise
except (OSError,EOFError,ValueError,KeyError,TypeError):
    info = None
if info is not None and Path(info.get('codexHome') or '').resolve() != root:
    raise control.ControlError('Native initialize belongs to another home')
after = control.inspect(root,executable,endpoint,None)
if before != after:
    raise control.ControlError('Native generation changed during initialize')
configuration = {}
for name in ('config.toml','auth.json'):
    try:
        configuration[name] = hashlib.sha256((root/name).read_bytes()).hexdigest()
    except OSError:
        configuration[name] = None
result = {**after,'initialize':info,'platform':sys.platform,'configuration':configuration}
'''


def worker_source(request, *, inspect=False):
    """Load unmodified worker/reader modules without a remote bridge install."""
    modules = (
        ('bridge.platforms.linux.codex_daemon_env', 'platforms/linux/codex_daemon_env.py'),
        ('bridge.platforms.linux.codex_cli_control', 'platforms/linux/codex_cli_control.py'),
        ('bridge.clients.websocket', 'clients/websocket.py'))
    source = 'import json,sys,types,socket,struct,os,hashlib\nfrom pathlib import Path\n'
    source += 'for name in ("bridge","bridge.platforms","bridge.platforms.linux","bridge.clients"):\n'
    source += ' module=types.ModuleType(name); module.__path__=[]; sys.modules[name]=module\n'
    for name, path in modules:
        source += ('module=types.ModuleType(' + repr(name) + '); sys.modules[module.__name__]=module\n'
                   'exec(compile(' + repr(source_text(path)) + ',' + repr(path) + ',"exec"),module.__dict__)\n')
    source += ('from bridge.platforms.linux import codex_cli_control as control\n'
               'from bridge.clients.websocket import WebSocket\nrequest=json.loads(' + repr(json.dumps(request)) + ')\n')
    operation = _INSPECT if inspect else 'result=control.run(request)\n'
    source += 'try:\n' + ''.join(' '+line+'\n' for line in operation.splitlines())
    source += (' print(json.dumps({"ok":True,"result":result}))\n'
               'except (OSError,ValueError,KeyError,TypeError) as error:\n'
               ' print(json.dumps({"ok":False,"errorType":type(error).__name__,'
               '"error":str(error) if isinstance(error,control.ControlError) else "Target lifecycle state unavailable"}))\n')
    return source


class LinuxCliControl:
    def __init__(self, home, reader):
        self.home, self.reader = home, reader
        self.lock = threading.RLock()
        self.value = None
        self.refreshing = False

    def _read(self, request, *, inspect=False):
        try:
            reply = self.reader(worker_source(request, inspect=inspect), timeout=18)
        except (OSError, RuntimeError, IPCError) as error:
            raise ValueError('目标生命周期传输结果尚未核验；不会重发操作') from error
        if not isinstance(reply, dict) or reply.get('ok') is not True:
            raise ValueError('目标 Linux 原生进程或生命周期回执尚未核验')
        return reply['result']

    def inspect(self):
        home = self.home()
        if home is not None and (not isinstance(home, str) or not home.startswith('/')):
            raise ReleaseNotSent('目标 CLI home 尚未通过原生连接核验')
        result = self._read({'home': home}, inspect=True)
        if result.get('platform') != 'linux':
            raise ReleaseNotSent('目标不是已核验的 Linux 生命周期运行时')
        info = result.get('initialize') or {}
        known_version = isinstance(info.get('userAgent'), str)
        result['generation']['nativeVersion'] = daemon_version(info) if known_version else None
        result['canRelease'] = bool(result.get('canForce') and known_version and graceful_restart_supported(info, platform='linux'))
        with self.lock:
            self.value = copy.deepcopy(result)
        return result

    def cached(self):
        with self.lock:
            return copy.deepcopy(self.value)

    def refresh(self):
        """Only called after an explicit connection, never by passive listings."""
        with self.lock:
            if self.refreshing:
                return
            self.refreshing = True
        def read():
            try:
                self.inspect()
            except (ValueError, OSError):
                pass
            finally:
                with self.lock:
                    self.refreshing = False
        threading.Thread(target=read, daemon=True, name='cli-target-identity').start()

    def dispatch(self, request_id, mode, expected):
        return self._read({'action':'dispatch','home':expected['home'],'expected':expected,
                          'requestId':request_id,'mode':mode,'timeout':0})

    def check(self, request_id, expected):
        return self._read({'action':'check','home':expected['home'],'expected':expected,
                          'requestId':request_id,'timeout':0})
