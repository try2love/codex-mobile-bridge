#!/usr/bin/env python3
"""Build the platform's visible Claude Console bootstrap, never run it."""
import os
import shutil
import subprocess
import sys
from pathlib import Path


def build():
    root = Path(__file__).resolve().parents[1]
    source = root/'bridge/platforms'
    target = root/'dist/client-helpers'
    target.mkdir(parents=True, exist_ok=True)
    if sys.platform == 'darwin':
        compiler = shutil.which('swiftc')
        if not compiler:
            raise RuntimeError('Swift compiler required for the macOS Claude connector')
        cache = root/'.tmp/claude-helper/module-cache'
        cache.mkdir(parents=True, exist_ok=True)
        subprocess.run([compiler, '-O', '-module-cache-path', str(cache),
                        str(source/'macos/claude-helper.swift'), '-o', str(target/'claude-bridge-helper')], check=True)
        subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', str(target/'claude-bridge-helper')], check=True)
        subprocess.run([str(target/'claude-bridge-helper'), '--self-check'], check=True)
    elif sys.platform == 'win32':
        framework = Path(os.environ.get('WINDIR', 'C:/Windows'))/'Microsoft.NET/Framework64/v4.0.30319'
        subprocess.run([str(framework/'csc.exe'), '/nologo', '/target:exe', '/platform:x64',
                        '/out:'+str(target/'claude-bridge-helper.exe'),
                        *['/reference:'+str(framework/'WPF'/name) for name in
                          ['UIAutomationClient.dll', 'UIAutomationTypes.dll', 'WindowsBase.dll']],
                        str(source/'windows/claude-helper.cs')], check=True)
        subprocess.run([str(target/'claude-bridge-helper.exe'), '--self-check'], check=True)
    return target


if __name__ == '__main__':
    build()
