"""Explicit, authenticated command jobs. No Codex owner or agent is started."""
import codecs
import hashlib
import json
import os
from pathlib import Path
import queue
import shlex
import signal
import subprocess
import sys
import threading
import time
import uuid

MAX_OUTPUT = 256 * 1024
MAX_SECONDS = 1800


class CommandJob:
    def __init__(self, root, command):
        self.lock = threading.RLock()
        self.text = ''; self.end = 0; self.code = None; self.reason = ''
        self.done = threading.Event(); self.created = time.monotonic()
        self.command = command; self.root = str(root)
        if os.name == 'nt':
            args = [os.environ.get('COMSPEC', 'cmd.exe'), '/d', '/s', '/c', command]
            options = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP}
        else:
            args = ['/bin/sh', '-c', command]
            options = {'start_new_session': True}
        self.process = subprocess.Popen(args, cwd=root, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0,
            env={**os.environ, 'TERM':'dumb', 'NO_COLOR':'1', 'PYTHONUNBUFFERED':'1'}, **options)
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._timeout, daemon=True).start()

    def append(self, text):
        with self.lock:
            self.end += len(text); self.text = (self.text + text)[-MAX_OUTPUT:]

    def _read(self):
        decoder = codecs.getincrementaldecoder('utf-8')('replace')
        try:
            while True:
                data = self.process.stdout.read(8192)
                if not data: break
                self.append(decoder.decode(data))
            self.append(decoder.decode(b'', final=True))
            self.code = self.process.wait()
        finally:
            self.process.stdout.close(); self.done.set()

    def _timeout(self):
        if not self.done.wait(MAX_SECONDS): self.stop('命令已达到 30 分钟运行上限')

    def _signal(self, force=False):
        try:
            if os.name == 'nt':
                if self.process.poll() is None:
                    subprocess.run(['taskkill', '/pid', str(self.process.pid), '/t', '/f'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
            else:
                os.killpg(self.process.pid, signal.SIGKILL if force else signal.SIGTERM)
        except (ProcessLookupError, OSError, subprocess.TimeoutExpired):
            if self.process.poll() is None: self.process.kill()

    def stop(self, reason='已停止'):
        if self.done.is_set(): return
        self.reason = reason; self._signal()
        def finish():
            if not self.done.wait(1): self._signal(True)
        threading.Thread(target=finish, daemon=True).start()

    def read(self, after=0):
        with self.lock:
            start = self.end - len(self.text)
            reset = after < start or after > self.end
            return {'output': self.text if reset else self.text[after-start:], 'cursor':self.end,
                    'truncated':start > 0, 'reset':reset, 'running':not self.done.is_set(),
                    'exitCode':self.code, 'message':self.reason}


def serve_remote(root, command):
    """SSH stdin owns the job: disconnect or stop cancels its process group."""
    job = CommandJob(root, command); incoming = queue.Queue()
    def receive():
        try:
            pending = b''
            while True:
                chunk = os.read(sys.stdin.fileno(), 1024)
                if not chunk: break
                pending += chunk
                while b'\n' in pending:
                    line, pending = pending.split(b'\n', 1)
                    incoming.put(json.loads(line))
        finally: incoming.put({'action':'stop'})
    threading.Thread(target=receive, daemon=True).start()
    cursor = 0
    try:
        while True:
            event = job.read(cursor); cursor = event['cursor']
            print(json.dumps(event), flush=True)
            if not event['running']: break
            try:
                if incoming.get(timeout=.3).get('action') == 'stop': job.stop()
            except queue.Empty: pass
    finally:
        job.stop(); job.done.wait(3)


class RemoteCommandJob(CommandJob):
    def __init__(self, alias, root, command):
        if not alias or alias.startswith('-') or any(c.isspace() for c in alias):
            raise ValueError('SSH 别名无效')
        self.lock = threading.RLock(); self.text = ''; self.end = 0; self.code = None; self.reason = ''
        self.done = threading.Event(); self.created = time.monotonic(); self.command = command; self.root = str(root)
        source = Path(__file__).with_name('terminal.py').read_text(encoding='utf-8')
        source += '\nserve_remote(' + repr(str(root)) + ', ' + repr(command) + ')\n'
        args = ['ssh','-T','-o','BatchMode=yes','-o','ClearAllForwardings=yes','-o','ConnectTimeout=8',
                '-o','StrictHostKeyChecking=yes','-o','UpdateHostKeys=no',alias,'python3 -u -c ' + shlex.quote(source)]
        self.process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, start_new_session=os.name != 'nt')
        threading.Thread(target=self._remote_read, daemon=True).start()
        threading.Thread(target=self._errors, daemon=True).start()
        threading.Thread(target=self._timeout, daemon=True).start()

    def _errors(self):
        # Do not forward SSH diagnostics containing private connection details.
        try:
            while self.process.stderr.read(8192): pass
        finally: self.process.stderr.close()

    def _remote_read(self):
        finished = False
        try:
            for line in self.process.stdout:
                event = json.loads(line)
                if event['reset']:
                    with self.lock: self.text = event['output'][-MAX_OUTPUT:]; self.end = event['cursor']
                else: self.append(event['output'])
                if not event['running']:
                    self.code = event['exitCode']; self.reason = event['message']; finished = True
            if not finished: self.reason = 'SSH 连接已断开，请检查服务器上的执行结果；不要直接重复执行。'
        except (ValueError, KeyError):
            self.reason = 'SSH 输出异常，请检查服务器上的执行结果。'
        finally:
            self.process.stdout.close()
            if not finished: self.process.kill()
            self.process.wait(); self.process.stdin.close(); self.done.set()

    def stop(self, reason='已停止'):
        if self.done.is_set(): return
        self.reason = reason
        try:
            with self.lock:
                self.process.stdin.write(b'{"action":"stop"}\n'); self.process.stdin.flush()
        except (BrokenPipeError, ValueError, OSError): pass
        def finish():
            if not self.done.wait(4): self.process.kill()
        threading.Thread(target=finish, daemon=True).start()


class TerminalManager:
    def __init__(self):
        self.lock = threading.RLock(); self.jobs = {}; self.seen = {}; self.sessions = {}; self.closed = set()

    def start(self, owner, thread, identifier, root, command, alias=None):
        uuid.UUID(identifier)
        if not isinstance(command, str) or not command.strip() or len(command) > 16000 or '\x00' in command:
            raise ValueError('请输入有效命令（最多 16,000 字符）')
        if not root or (not alias and not Path(root).is_dir()): raise ValueError('聊天项目目录不可用')
        key = (owner, thread, identifier); fingerprint = hashlib.sha256((str(root)+'\0'+command).encode()).hexdigest()
        with self.lock:
            if key in self.seen:
                if self.seen[key] != fingerprint: raise ValueError('执行编号已用于另一条命令')
                if key not in self.jobs: raise ValueError('此命令记录已过期，不会重复执行')
                return self.jobs[key].read()
            if len(self.seen) >= 4096: raise ValueError('本次网关的命令记录已满，请重启网关后继续')
            active = [k for k,j in self.jobs.items() if not j.done.is_set()]
            if len(active) >= 16 or sum(k[0] == owner for k in active) >= 4: raise ValueError('请先停止或等待正在运行的命令')
            job = RemoteCommandJob(alias, root, command) if alias else CommandJob(root, command)
            self.jobs[key] = job; self.seen[key] = fingerprint
            for old in list(self.jobs):
                if len(self.jobs) <= 32: break
                if old != key and self.jobs[old].done.is_set(): del self.jobs[old]
            return job.read()

    def get(self, owner, thread, identifier):
        with self.lock:
            job = self.jobs.get((owner, thread, identifier))
            if job is None: raise KeyError('命令不存在或不属于当前登录；网关重启后记录会清除')
            return job

    def open_session(self, owner, thread, identifier, root, cols, rows, alias=None):
        from .pty_terminal import TerminalSession, RemoteTerminalSession, dimensions
        uuid.UUID(identifier); dimensions(cols, rows)
        key = (owner, thread, identifier)
        with self.lock:
            if key in self.closed: raise ValueError('此终端已关闭，请打开新终端')
            if key in self.sessions: return self.sessions[key].read()
            if not root or (not alias and not Path(root).is_dir()): raise ValueError('聊天项目目录不可用')
            active = [k for k, job in self.sessions.items() if not job.done.is_set()]
            if len(active) >= 16 or sum(k[0] == owner for k in active) >= 4: raise ValueError('请先关闭其他终端')
            if len(self.closed) >= 4096: raise ValueError('终端记录已满，请重启网关')
            for old in list(self.sessions):
                if self.sessions[old].done.is_set(): self.closed.add(old); del self.sessions[old]
            self.sessions[key] = RemoteTerminalSession(alias, root, cols, rows) if alias else TerminalSession(root, cols, rows)
            return self.sessions[key].read()

    def session(self, owner, thread, identifier):
        with self.lock:
            session = self.sessions.get((owner, thread, identifier))
            if session is None: raise KeyError('终端已关闭或不属于当前登录，请重新打开')
            return session

    def close(self):
        with self.lock: jobs = list(self.jobs.values()) + list(self.sessions.values())
        for job in jobs: job.stop('网关已停止')
        deadline = time.monotonic() + 5
        for job in jobs: job.done.wait(max(0, deadline-time.monotonic()))
