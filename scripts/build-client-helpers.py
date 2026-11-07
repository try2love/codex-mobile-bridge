#!/usr/bin/env python3
"""Build and self-check native client lifecycle helpers without controlling apps."""
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
            raise RuntimeError('Swift compiler required for macOS client helpers')
        cache = root/'.tmp/claude-helper/module-cache'
        cache.mkdir(parents=True, exist_ok=True)
        for native_source, output_name in [(source/'macos/claude-helper.swift', 'claude-bridge-helper'),
                                           (source/'macos/zcode-helper.swift', 'zcode-bridge-helper')]:
            helper = str(target/output_name)
            subprocess.run([compiler, '-O', '-module-cache-path', str(cache), str(native_source), '-o', helper], check=True)
            subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', helper], check=True)
            subprocess.run([helper, '--self-check'], check=True, timeout=10)
    elif sys.platform == 'win32':
        framework = Path(os.environ.get('WINDIR', 'C:/Windows'))/'Microsoft.NET/Framework64/v4.0.30319'
        for native_source, output_name in [(source/'windows/claude-helper.cs', 'claude-bridge-helper.exe'),
                                           (source/'windows/codex-quit-helper.cs', 'codex-quit-helper.exe')]:
            subprocess.run([str(framework/'csc.exe'), '/nologo', '/target:exe', '/platform:x64',
                            '/out:'+str(target/output_name),
                            *['/reference:'+str(framework/'WPF'/name) for name in
                              ['UIAutomationClient.dll', 'UIAutomationTypes.dll', 'WindowsBase.dll']],
                            str(native_source)], check=True)
            subprocess.run([str(target/output_name), '--self-check'], check=True)
    return target


if __name__ == '__main__':
    build()
