import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch

from bridge.pty_terminal import TerminalSession, RemoteTerminalSession, default_shell, dimensions
from bridge.terminal import TerminalManager


@unittest.skipIf(os.name == 'nt', 'POSIX PTY requires Unix')
class PtyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp')
        self.root = Path(self.temp.name); self.sessions = []
        self.env = patch.dict(os.environ, {'ZDOTDIR':'.', 'HISTFILE':str(self.root/'history')}); self.env.start()
        # Keep distro-wide interactive setup (e.g. compinit prompts) out of the fixture.
        (self.root/'.zshenv').write_text('unsetopt GLOBAL_RCS\n')
        (self.root/'.zshrc').write_text("PROMPT='fixture% '\nexport BRIDGE_STARTUP_TEST=loaded\n")
    def tearDown(self):
        for session in self.sessions: session.stop(); self.assertTrue(session.done.wait(5))
        self.env.stop(); self.temp.cleanup()
    def session(self):
        session = TerminalSession(self.root); self.sessions.append(session); return session
    def send(self, session, data, identifier=None):
        session.write(identifier or str(uuid.uuid4()), data)
    def wait_output(self, session, marker):
        deadline = time.monotonic()+7
        while time.monotonic()<deadline:
            if marker in session.read()['output']: return session.read()['output']
            time.sleep(.02)
        self.fail('Missing terminal output '+marker+' in '+session.read()['output'][-1000:])
    def test_default_shell_uses_account_setting_not_inherited_shell(self):
        import pwd
        from types import SimpleNamespace
        for actual, inherited in (('/bin/zsh', '/bin/bash'), ('/bin/bash', '/bin/zsh')):
            with self.subTest(actual=actual), patch.object(pwd,'getpwuid',return_value=SimpleNamespace(pw_shell=actual)), patch.dict(os.environ,{'SHELL':inherited}):
                self.assertEqual(default_shell(),actual)
    def test_real_tty_startup_cd_environment_and_interactive_read(self):
        with patch('bridge.pty_terminal.default_shell',return_value='/bin/zsh'): s=self.session()
        self.send(s,"printf 'STARTUP:%s\\n' \"$BRIDGE_STARTUP_TEST\"; test -t 0 && echo PTY_OK\r")
        self.wait_output(s,'STARTUP:loaded'); self.wait_output(s,'PTY_OK\r\n')
        (self.root/'nested').mkdir()
        self.send(s,'cd nested; export BRIDGE_VALUE=kept\r')
        self.send(s,"printf 'STATE:%s:%s\\n' \"${PWD##*/}\" \"$BRIDGE_VALUE\"\r")
        self.wait_output(s,'STATE:nested:kept')
        self.send(s,"read reply; printf 'ANSWER:%s\\n' \"$reply\"\r")
        self.send(s,'hello input\r'); self.wait_output(s,'ANSWER:hello input')
    def test_resize_and_ctrl_c_keep_shell_alive(self):
        s=self.session(); s.resize(93,31)
        self.send(s,"stty size; sleep 90\r"); self.wait_output(s,'31 93')
        self.send(s,'\x03'); self.send(s,"printf 'AFTER_%s\\n' interrupt\r")
        self.wait_output(s,'AFTER_interrupt'); self.assertFalse(s.done.is_set())
        self.send(s,'exit\r'); self.assertTrue(s.done.wait(5))
    def test_input_retry_is_exactly_once_and_conflicts_rejected(self):
        s=self.session(); identifier=str(uuid.uuid4());data="printf x >> exactly-once\r"
        self.send(s,data,identifier);self.send(s,data,identifier)
        self.send(s,"printf 'ACK_%s\\n' ready\r");self.wait_output(s,'ACK_ready')
        self.assertEqual((self.root/'exactly-once').read_text(),'x')
        with self.assertRaises(ValueError):self.send(s,'echo different\r',identifier)
    def test_close_kills_foreground_process(self):
        s=self.session(); self.send(s,'sleep 90 & echo $! > child.pid; wait\r')
        deadline=time.monotonic()+5
        while not (self.root/'child.pid').exists() and time.monotonic()<deadline:time.sleep(.02)
        child=int((self.root/'child.pid').read_text());s.stop();self.assertTrue(s.done.wait(5))
        # The child may briefly be a zombie while reaped by the OS, but cannot run.
        result=subprocess.run(['ps','-p',str(child),'-o','stat='],capture_output=True,text=True)
        self.assertTrue(not result.stdout.strip() or result.stdout.strip().startswith('Z'))
    def test_remote_protocol_persists_state_and_acknowledges_input(self):
        original=subprocess.Popen
        def launch(args,**kwargs):
            self.assertEqual(args[:2],['ssh','-T'])
            return original([sys.executable,'-u','-c',shlex.split(args[-1])[-1]],**kwargs)
        with patch('bridge.pty_terminal.subprocess.Popen',side_effect=launch): s=RemoteTerminalSession('fixture',self.root)
        self.sessions.append(s)
        self.send(s,'export REMOTE_VALUE=kept\r')
        self.send(s,"printf 'REMOTE:%s\\n' \"$REMOTE_VALUE\"\r")
        self.wait_output(s,'REMOTE:kept'); self.assertTrue(s.shell.startswith('/'))
        s.resize(91,29);self.send(s,'stty size\r');self.wait_output(s,'29 91')
    def test_manager_scopes_retries_and_does_not_resurrect_closed_terminal(self):
        m=TerminalManager();self.addCleanup(m.close); identifier=str(uuid.uuid4())
        m.open_session('a','thread',identifier,self.root,80,24);s=m.session('a','thread',identifier)
        m.open_session('a','thread',identifier,self.root,80,24);self.assertIs(s,m.session('a','thread',identifier))
        with self.assertRaises(KeyError):m.session('b','thread',identifier)
        with self.assertRaises(KeyError):m.session('a','other',identifier)
        s.stop();self.assertTrue(s.done.wait(5));self.assertFalse(m.open_session('a','thread',identifier,self.root,80,24)['running'])
        for pair in ((0,24),(80,999),(True,20)):
            with self.assertRaises(ValueError):dimensions(*pair)
