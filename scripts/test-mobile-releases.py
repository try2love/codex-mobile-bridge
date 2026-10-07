#!/usr/bin/env python3
"""Exercise native mobile update selection without devices or network calls."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
work = root / '.tmp/mobile-release-tests'
work.mkdir(parents=True, exist_ok=True)
jdk = next((root / '.tmp/mobile-tools/jdk').glob('*/Contents/Home'), None)
def java_tool(name):
    return str(jdk / 'bin' / name) if jdk else shutil.which(name)
subprocess.run([java_tool('javac'), '-d', str(work), str(root / 'mobile/android/src/io/github/try2love/codexbridge/MobileRelease.java'), str(root / 'mobile/tests/MobileReleaseTests.java')], check=True)
subprocess.run([java_tool('java'), '-cp', str(work), 'MobileReleaseTests'], check=True)
if sys.platform == 'darwin':
    subprocess.run(['xcrun', 'swiftc', '-module-cache-path', str(work / 'cache'), str(root / 'mobile/ios/BridgePreview/MobileRelease.swift'), str(root / 'mobile/tests/MobileReleaseTests.swift'), '-o', str(work / 'swift-tests')], check=True)
    subprocess.run([str(work / 'swift-tests')], check=True)
