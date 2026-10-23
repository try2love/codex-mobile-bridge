"""WSL inventory and byte streams; daemon startup requires an explicit action."""
import json
import os
import subprocess
import threading
import tempfile
from contextlib import contextmanager
from concurrent.futures import Future, TimeoutError as FutureTimeout


def _options():
    return {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}


def _distributions(running=True):
    if os.name != 'nt':
        return []
    try:
        result = subprocess.run(['wsl.exe', '--list', *(['--running'] if running else []), '--quiet'],
                                capture_output=True, timeout=8, **_options())
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode:
        return []  # Missing WSL/no distros is not a reason to break desktop reads.
    data = result.stdout
    text = data.decode('utf-16-le' if b'\0' in data else 'utf-8-sig', errors='strict')
    return [s.strip().lstrip('\ufeff') for s in text.splitlines() if s.strip().lstrip('\ufeff')]


def running_distributions():
    return _distributions()


def command(distribution, user=None, allow_start=False):
    # Listing is repeated immediately before every invocation. Discovery is not
    # permission to wake a distribution later during a background request.
    available = _distributions(False) if allow_start else running_distributions()
    if not isinstance(distribution, str) or distribution not in available:
        raise OSError('WSL 发行版已停止或不可用，请在电脑端启动后重新连接')
    args = ['wsl.exe', '--distribution', distribution]
    if user is not None:
        if not isinstance(user, str) or not user or user.startswith('-') or any(c in user for c in '\0\r\n'):
            raise ValueError('WSL 用户无效')
        args += ['--user', user]
    return args + ['--exec', 'python3']


def read(distribution, source, user=None, timeout=20, allow_start=False):
    try:
        result = subprocess.run(command(distribution, user, allow_start=allow_start) + ['-'], input=source.encode('utf-8'),
                                capture_output=True, timeout=timeout, **_options())
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError('WSL 读取超时') from exc
    if result.returncode:
        raise OSError('WSL 读取失败，请检查发行版中的 Python 和用户权限')
    if len(result.stdout) > 24 * 1024 * 1024:
        raise OSError('WSL 响应过大')
    try:
        return json.loads(result.stdout.decode('utf-8'))
    except (ValueError, UnicodeError) as exc:
        raise OSError('WSL 返回了无法识别的数据') from exc


# Only same-user process identities and native filesystem metadata are returned.
# Do not return command lines or general environment/configuration contents.
_INVENTORY = r'''
import json,os,pwd,stat,subprocess
from pathlib import Path
uid=os.getuid(); user=pwd.getpwuid(uid).pw_name; home=Path.home()
homes={str((home/'.codex').resolve()):None}
unix={}
for line in Path('/proc/net/unix').read_text().splitlines()[1:]:
    f=line.split()
    if len(f)==8 and f[3]=='00010000': unix[f[6]]=f[7]
for p in Path('/proc').iterdir():
    if not p.name.isdigit(): continue
    try:
        if p.stat().st_uid!=uid: continue
        exe=os.readlink(p/'exe'); a=(p/'cmdline').read_bytes().split(b'\0')
        if Path(exe).name!='codex' or b'app-server' not in a or b'--managed-daemon' not in a: continue
        env=dict(s.split(b'=',1) for s in (p/'environ').read_bytes().split(b'\0') if b'=' in s)
        root=Path(os.fsdecode(env.get(b'CODEX_HOME',os.fsencode(home/'.codex')))).resolve()
        for fd in (p/'fd').iterdir():
            target=os.readlink(fd)
            path=unix.get(target[8:-1]) if target.startswith('socket:[') else None
            if not path: continue
            sock=Path(path); st=sock.lstat()
            if not stat.S_ISSOCK(st.st_mode) or st.st_uid!=uid or st.st_mode & 0o077: continue
            if sock.parent!=Path('/tmp/codex-daemon-'+str(uid)): continue
            parent=sock.parent.lstat()
            if not stat.S_ISDIR(parent.st_mode) or parent.st_uid!=uid or parent.st_mode & 0o022: continue
            homes[str(root)]={'socket':path,'pid':int(p.name),'executable':exe}
    except (OSError,ValueError): continue
rows=[]
for h,d in homes.items():
    if not Path(h).is_dir():continue
    windows=None
    try:
        result=subprocess.run(['wslpath','-w',h],capture_output=True,text=True,timeout=3)
        if not result.returncode:windows=result.stdout.strip()
    except (OSError,subprocess.TimeoutExpired):pass
    rows.append({'home':h,'daemon':d,'windowsHome':windows})
inventory={'user':user,'userHome':str(home),'homes':rows}
'''
PROBE = _INVENTORY + '\nprint(json.dumps(inventory))\n'


def probe(distribution, user=None):
    return read(distribution, PROBE, user)


_START_DAEMON = r'''
import shutil,re
def vscode_app_server(executable,args,parent_executable,parent_args):
    # Only a VS Code extension host's private stdio server can coexist. Unknown
    # app-servers and terminal executors still block shared-daemon startup.
    for directory in ('.vscode-server','.vscode-server-insiders'):
        base=home/directory
        try:
            relative=Path(executable).relative_to(base).as_posix()
            parent=Path(parent_executable).relative_to(base).as_posix()
        except ValueError: continue
        if not re.fullmatch(r'extensions/openai\.chatgpt-[A-Za-z0-9.-]+/bin/linux-(?:x86_64|aarch64)/codex',relative): continue
        if not re.fullmatch(r'bin/[a-f0-9]+/node',parent) or b'--type=extensionHost' not in parent_args: continue
        remaining=[]; i=1
        while i<len(args):
            if args[i] in (b'-c',b'--config') and i+1<len(args): i+=2; continue
            if args[i]: remaining.append(args[i])
            i+=1
        return remaining in ([b'app-server'],[b'app-server',b'--analytics-default-enabled'])
    return False

root=Path(requested_home).resolve()
if not root.is_dir() or root.stat().st_uid!=uid: raise ValueError('Codex configuration directory unavailable')
existing=[r for r in inventory['homes'] if r.get('daemon')]
if not any(r['home']==str(root) for r in existing):
    if existing: raise ValueError('Another configuration owns the shared daemon')
    candidates=[]
    # A private/legacy executor may still own work in this configuration. Never
    # start a competing daemon when its task state cannot be verified.
    for p in Path('/proc').iterdir():
        if not p.name.isdigit(): continue
        try:
            if p.stat().st_uid!=uid: continue
            exe=os.readlink(p/'exe')
            if Path(exe).name!='codex': continue
            env=dict(s.split(b'=',1) for s in (p/'environ').read_bytes().split(b'\0') if b'=' in s)
            actual=Path(os.fsdecode(env.get(b'CODEX_HOME',os.fsencode(home/'.codex')))).resolve()
            if actual!=root: continue
            isolated=False
            try:
                parent=Path('/proc')/(p/'stat').read_text().rsplit(') ',1)[1].split()[1]
                isolated=(parent.stat().st_uid==uid and vscode_app_server(exe,(p/'cmdline').read_bytes().split(b'\0'),
                          os.readlink(parent/'exe'),(parent/'cmdline').read_bytes().split(b'\0')))
            except (OSError,ValueError,IndexError): pass
            if not isolated: raise RuntimeError('An independent Codex executor is already running')
        except (OSError,ValueError): continue
    found=shutil.which('codex')
    if found:candidates.append(Path(found))
    candidates.extend(home.glob('.nvm/versions/node/*/bin/codex'))
    candidates.extend([home/'.local/bin/codex',home/'.codex/bin/codex',Path('/usr/local/bin/codex')])
    packages=root/'packages/app-server-daemon/releases'
    def version(p):return tuple(int(n) for n in re.match(r'(\d+)\.(\d+)\.(\d+)',p.parent.parent.name).groups())
    cached=[p for p in packages.glob('*/bin/codex') if re.match(r'\d+\.\d+\.\d+',p.parent.parent.name)]
    candidates.extend(sorted(cached,key=version,reverse=True))
    environment=dict(os.environ,CODEX_HOME=str(root))
    executable=None
    for candidate in dict.fromkeys(candidates):
        if not candidate.is_file() or not os.access(candidate,os.X_OK):continue
        try:
            check=subprocess.run([str(candidate.resolve()),'app-server','daemon','start','--help'],env=environment,
                                 stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=12)
            if not check.returncode:executable=candidate.resolve();break
        except (OSError,subprocess.TimeoutExpired):continue
    if not executable: raise ValueError('No installed Codex runtime supports shared daemon startup')
    subprocess.run([str(executable),'app-server','daemon','start'],env=environment,cwd=root,
                   stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=45,check=True)
'''


def ensure_daemon(distribution, user, home):
    """Explicit open/create only. Use native idempotent start, never restart/update."""
    source = 'requested_home=' + repr(home) + '\n' + _INVENTORY + _START_DAEMON
    source += '\n' + _INVENTORY + '\nprint(json.dumps(inventory))\n'
    return read(distribution, source, user, timeout=65, allow_start=True)


@contextmanager
def download(distribution, source, user=None):
    with tempfile.TemporaryFile(prefix='bridge-wsl-download-') as stream:
        try:
            result = subprocess.run(command(distribution, user) + ['-'], input=source.encode('utf-8'),
                                    stdout=stream, stderr=subprocess.DEVNULL, timeout=60, **_options())
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError('WSL 下载超时') from exc
        if result.returncode:
            raise OSError('WSL 下载失败')
        size = stream.tell()
        stream.seek(0)
        header = stream.readline(8193)
        if not header.endswith(b'\n') or len(header) > 8192:
            raise ValueError('WSL 下载描述无效')
        metadata = json.loads(header)
        if 'error' in metadata:
            if metadata.get('permission'):
                raise PermissionError(metadata['error'])
            raise ValueError(metadata['error'])
        if type(metadata.get('length')) is not int or not 0 <= metadata['length'] <= 2**53 - 1 or metadata['length'] != size - len(header):
            raise ValueError('WSL 下载不完整')
        yield metadata, stream


RELAY = r'''
import os,socket,stat,struct,sys,threading
from pathlib import Path
path,root,pid=sys.argv[1:]; uid=os.getuid(); p=Path('/proc')/pid
st=Path(path).lstat(); parent=Path(path).parent.lstat()
if not stat.S_ISSOCK(st.st_mode) or st.st_uid!=uid or st.st_mode & 0o077: sys.exit(2)
if not stat.S_ISDIR(parent.st_mode) or parent.st_uid!=uid or parent.st_mode & 0o022: sys.exit(2)
if p.stat().st_uid!=uid or Path(os.readlink(p/'exe')).name!='codex': sys.exit(2)
a=(p/'cmdline').read_bytes().split(b'\0')
if b'app-server' not in a or b'--managed-daemon' not in a: sys.exit(2)
env=dict(s.split(b'=',1) for s in (p/'environ').read_bytes().split(b'\0') if b'=' in s)
actual=Path(os.fsdecode(env.get(b'CODEX_HOME',os.fsencode(Path.home()/'.codex')))).resolve()
if actual!=Path(root).resolve(): sys.exit(2)
s=socket.socket(socket.AF_UNIX); s.connect(path)
peer=struct.unpack('3i',s.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
if peer[0]!=int(pid) or peer[1]!=uid: sys.exit(2)
def upload():
    try:
        while True:
            data=os.read(0,65536)
            if not data: break
            s.sendall(data)
    finally:
        try:s.shutdown(socket.SHUT_WR)
        except OSError:pass
threading.Thread(target=upload,daemon=True).start()
while True:
    data=s.recv(65536)
    if not data:break
    view=memoryview(data)
    while view:
        n=os.write(1,view);view=view[n:]
'''


class Stream:
    def __init__(self, distribution, user, home, daemon):
        self.process = subprocess.Popen(command(distribution, user) + [
            '-u', '-c', RELAY, daemon['socket'], home, str(daemon['pid'])],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **_options())
        self.buffer = bytearray()
        self.condition = threading.Condition()
        self.eof = False
        self.closed = False
        self.timeout = 12
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        try:
            while True:
                block = self.process.stdout.read1(65536)
                if not block:
                    break
                with self.condition:
                    while len(self.buffer) >= 64 * 1024 * 1024 and not self.closed:
                        self.condition.wait()
                    if self.closed:
                        break
                    self.buffer.extend(block)
                    self.condition.notify_all()
        except (OSError, ValueError):
            pass
        finally:
            with self.condition:
                self.eof = True
                self.condition.notify_all()

    def recv(self, size):
        with self.condition:
            if not self.condition.wait_for(lambda: self.buffer or self.eof or self.closed, timeout=self.timeout):
                raise TimeoutError('WSL 字节流超时')
            result = bytes(self.buffer[:size])
            del self.buffer[:size]
            self.condition.notify_all()
            return result

    def sendall(self, data):
        if self.closed:
            raise OSError('WSL 字节流已关闭')
        completed = Future()
        def write():
            try:
                self.process.stdin.write(data)
                self.process.stdin.flush()
                completed.set_result(None)
            except (OSError, ValueError) as exc:
                completed.set_exception(OSError('WSL 字节流写入失败'))
        threading.Thread(target=write, daemon=True).start()
        try:
            completed.result(timeout=12)
        except FutureTimeout as exc:
            self.close()
            raise TimeoutError('WSL 字节流写入超时，结果待核对') from exc

    def settimeout(self, timeout):
        self.timeout = timeout

    def close(self):
        with self.condition:
            if self.closed:
                return
            self.closed = True
            self.condition.notify_all()
        # Terminate only our relay before closing a possibly blocked buffered
        # writer. The original daemon/TUI is never signalled.
        if self.process.poll() is None:
            self.process.terminate()
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=3)
        try:
            self.process.stdin.close()
        except (OSError, ValueError):
            pass
        self.reader.join(timeout=2)
        self.process.stdout.close()
