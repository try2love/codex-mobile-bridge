#!/usr/bin/env python3
"""Explicit Linux GUI smoke using the installed App and an isolated profile.

The real system desktop entry and running default App are preserved. Requires
the existing user's GUI environment. No account access or model calls.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--area',required=True)
    parser.add_argument('--application',required=True)
    parser.add_argument('--cli',required=True)
    parser.add_argument('--desktop',required=True)
    args=parser.parse_args()
    area=Path(args.area).resolve();area.mkdir(parents=True,exist_ok=True)
    from bridge.clients.codex import desktop_runtime as runtime
    from bridge.platforms.linux import codex_launch
    from bridge.platforms.linux.codex_runtime import identity
    app=Path(args.application).resolve();cli=Path(args.cli).resolve()
    original=Path(args.desktop);saved=original.read_bytes()
    before={}
    for folder in Path('/proc').glob('[0-9]*'):
        try:
            if folder.stat().st_uid!=os.getuid():continue
            row=identity(int(folder.name))
            if row['image']==str(app):before[row['pid']]=row
        except (OSError,ValueError):pass
    home=area/'profile $ launch';home.mkdir(mode=0o700,exist_ok=True)
    user=area/'xdg';system=area/'system/applications';system.mkdir(parents=True,exist_ok=True)
    # Preserve the actual PATH command while isolating Electron's profile lock.
    text=saved.decode()
    text='\n'.join(line+' --user-data-dir='+str(area/'electron') if line.startswith('Exec=') else line
                   for line in text.splitlines())+'\n'
    (system/original.name).write_text(text)
    os.environ.update(XDG_DATA_HOME=str(user),XDG_DATA_DIRS=str(system.parent),
                      CODEX_ELECTRON_USER_DATA_PATH=str(area/'electron'),CODEX_SQLITE_HOME=str(home))
    result={'complete':False,'modelRequests':0,'opens':[],'originalAppPids':list(before)}
    owned=None
    def wait(check,label,seconds=30):
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            value=check()
            if value:return value
            time.sleep(.2)
        raise TimeoutError(label)
    def ready():
        try:
            record=runtime._read(runtime.directory(home)/'active.json')
            if record.get('ready'):
                runtime.verify_record(record);return record
        except (OSError,ValueError,KeyError):return None
    try:
        runtime.configure(home,app,cli)
        assert runtime.enable_ordinary_launch(home,app)
        entry=user/'applications'/original.name
        # Use the desktop implementation's actual parser and launcher.
        subprocess.run(['desktop-file-validate',str(entry)],check=True,timeout=10)
        for attempt in range(2):
            started=time.monotonic()
            with (area/('gui-'+str(attempt)+'.log')).open('wb') as log:
                subprocess.run(['gio','launch',str(entry)],stdout=log,stderr=log,check=True,timeout=15)
            owned=wait(ready,'ordinary menu did not establish native connection')
            assert owned['app']['pid'] not in before
            client=runtime.connect(home,str(cli))
            try:
                assert isinstance(client.request('thread/list',{'limit':1},timeout=8)['data'],list)
            finally:client.close()
            result['opens'].append({'attempt':attempt+1,'readyMs':round((time.monotonic()-started)*1000,1),
                'nativeReadSucceeded':True,'transport':owned['transport']})
            runtime.verify_record(owned)
            os.kill(owned['app']['pid'],signal.SIGTERM)
            def stopped():
                try:identity(owned['app']['pid']);return False
                except ProcessLookupError:return True
            wait(stopped,'isolated App did not exit normally')
            runtime.wait_stopped(home);owned=None
        assert original.read_bytes()==saved
        assert all(identity(pid)==row for pid,row in before.items())
        result.update(complete=True,systemDesktopUnchanged=True,originalAppPreserved=True)
    except BaseException as error:
        result['error']=type(error).__name__+': '+str(error)
        raise
    finally:
        if owned:
            try:
                runtime.verify_record(owned);os.kill(owned['app']['pid'],signal.SIGTERM)
            except (OSError,ValueError):pass
        (area/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
