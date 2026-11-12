#!/usr/bin/env python3
"""Run menu instrumentation on a disposable emulator; clears that emulator's Test preferences."""
import argparse
import os
from pathlib import Path
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--adb', required=True, type=Path)
parser.add_argument('--serial', required=True, help='Explicit disposable emulator serial (emulator-NNNN)')
parser.add_argument('--apk', required=True, type=Path, help='Current preview APK signed by the local preview key')
parser.add_argument('--keystore', required=True, type=Path)
parser.add_argument('--store-pass-env', default='CMB_ANDROID_STORE_PASSWORD')
parser.add_argument('--application-id', default='io.github.try2love.codexbridge.preview')
args = parser.parse_args()
if not args.serial.startswith('emulator-') or not args.serial[9:].isdigit():
    parser.error('Only an explicitly selected disposable emulator is supported.')
tools = ROOT / '.tmp/mobile-tools'
jdk = next((tools / 'jdk').glob('*/Contents/Home'))
bt = next(p for p in (tools / 'sdk/build-tools').iterdir() if (p / 'aapt2').exists())
android = next((tools / 'sdk/platforms').glob('*/android.jar'))
work = ROOT / '.tmp/android-gateway-menu-tests'
work.mkdir(parents=True, exist_ok=True)
env = {**os.environ, 'JAVA_HOME': str(jdk), 'PATH': str(jdk / 'bin') + os.pathsep + os.environ['PATH']}


def run(*command):
    subprocess.run([str(part) for part in command], cwd=ROOT, env=env, check=True)


def adb(*command):
    return [str(args.adb.resolve()), '-s', args.serial, *command]


manifest = work / 'AndroidManifest.xml'
manifest.write_text('<manifest xmlns:android="http://schemas.android.com/apk/res/android" '
                    'package="io.github.try2love.codexbridge.menutest">'
                    '<uses-sdk android:minSdkVersion="26" android:targetSdkVersion="35"/>'
                    '<application android:label="Menu regression"/>'
                    '<instrumentation android:name="io.github.try2love.codexbridge.menutest.AndroidGatewayMenuTests" '
                    'android:targetPackage="'+args.application_id+'"/></manifest>')
for name in ('classes', 'dex'):
    (work / name).mkdir(exist_ok=True)
run(bt / 'aapt2', 'link', '-I', android, '--manifest', manifest, '-o', work / 'base.apk')
run(jdk / 'bin/javac', '--release', '8', '-encoding', 'UTF-8', '-classpath', android,
    '-d', work / 'classes', ROOT / 'mobile/tests/AndroidGatewayMenuTests.java')
run(bt / 'd8', '--lib', android, '--min-api', '26', '--output', work / 'dex',
    *list((work / 'classes').rglob('*.class')))
with zipfile.ZipFile(work / 'base.apk', 'a') as archive:
    for file in (work / 'dex').glob('*.dex'):
        archive.write(file, file.name)
run(bt / 'zipalign', '-f', '4', work / 'base.apk', work / 'aligned.apk')
run(bt / 'apksigner', 'sign', '--ks', args.keystore, '--ks-pass', 'env:'+args.store_pass_env,
    '--out', work / 'test.apk', work / 'aligned.apk')
run(*adb('install', '-r', str(args.apk.resolve())))
run(*adb('install', '-r', str(work / 'test.apk')))
# Avoid a permission dialog hiding the tested window; only affects this disposable emulator.
run(*adb('shell', 'pm', 'grant', args.application_id, 'android.permission.POST_NOTIFICATIONS'))
result = subprocess.run(adb('shell', 'am', 'instrument', '-w',
                        'io.github.try2love.codexbridge.menutest/.AndroidGatewayMenuTests'),
                        capture_output=True, text=True, check=True, timeout=120)
(work / 'result.txt').write_text(result.stdout + result.stderr)
print(result.stdout)
run(*adb('pull', '/sdcard/Android/data/'+args.application_id+'/files/gateway-menu-tests', str(work)))
if 'result=PASS ' not in result.stdout or 'failure=' in result.stdout:
    raise SystemExit('Android menu instrumentation failed; see .tmp/android-gateway-menu-tests/result.txt')
