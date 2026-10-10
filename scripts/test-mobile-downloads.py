#!/usr/bin/env python3
"""Test Android downloads against isolated HTTP servers; no gateway credentials."""
import os
from pathlib import Path
import shutil
import subprocess

root = Path(__file__).resolve().parents[1]
work = root / '.tmp/mobile-download-tests'
work.mkdir(parents=True, exist_ok=True)
java_home = os.environ.get('JAVA_HOME')
local_jdks = list((root / '.tmp/mobile-tools/jdk').glob('*/Contents/Home'))
if not java_home and local_jdks:
    java_home = str(local_jdks[0])
def java_tool(name):
    return str(Path(java_home) / 'bin' / name) if java_home else shutil.which(name)
subprocess.run([java_tool('javac'), '-encoding', 'UTF-8', '-d', str(work),
                str(root / 'mobile/android/src/io/github/try2love/codexbridge/GatewayURL.java'),
                str(root / 'mobile/android/src/io/github/try2love/codexbridge/ArtifactDownload.java'),
                str(root / 'mobile/tests/ArtifactDownloadTests.java'),
                str(root / 'mobile/tests/ArtifactRangeTests.java')], check=True)
for test in ('ArtifactDownloadTests', 'ArtifactRangeTests'):
    subprocess.run([java_tool('java'), '-cp', str(work), test, str(work / test)], check=True)
