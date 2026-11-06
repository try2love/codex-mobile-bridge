#!/usr/bin/env python3
"""Build a local debug APK using project-local JDK/SDK; never installs tools."""
import hashlib
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output-dir', type=Path, default=ROOT/'dist/mobile-preview')
parser.add_argument('--cloud-libs', type=Path, help='Build the isolated PR132 Test with the prepared ARM64 JNI libraries and TLS classes.')
options = parser.parse_args()
tools = ROOT/'.tmp/mobile-tools'
jdks = list((tools/'jdk').glob('*/Contents/Home'))
if not jdks:
    raise SystemExit('Place a JDK 17 in .tmp/mobile-tools/jdk first.')
jdk = jdks[0]
sdk = tools/'sdk'
build_tools = next(p for p in (sdk/'build-tools').iterdir() if (p/'aapt2').exists())
android = next((sdk/'platforms').glob('*/android.jar'))
zxing = tools/'zxing-core-3.5.3.jar'
if hashlib.sha1(zxing.read_bytes()).hexdigest() != (tools/'zxing.sha1').read_text().strip():
    raise SystemExit('ZXing checksum mismatch')
work = ROOT/'.tmp/mobile-preview'/('android-cloud-build' if options.cloud_libs else 'android-build')
work.mkdir(parents=True, exist_ok=True)
for name in ('gen', 'classes', 'dex'):
    folder = work/name
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir()
env = {**os.environ, 'JAVA_HOME': str(jdk), 'PATH': str(jdk/'bin')+os.pathsep+os.environ['PATH']}
def run(*args):
    subprocess.run([str(a) for a in args], env=env, check=True, cwd=ROOT)
source = ROOT/'mobile/android'
run(build_tools/'aapt2','compile','--dir',source/'res','-o',work/'resources.zip')
manifest = source/'AndroidManifest.xml'
if options.cloud_libs:
    manifest = work/'AndroidManifest.xml'
    manifest.write_text((source/manifest.name).read_text().replace('io.github.try2love.codexbridge.preview','io.github.try2love.codexbridge.cloudprototype').replace('android:label="Codex Bridge"','android:label="Bridge Cloud Test"'))
run(build_tools/'aapt2','link','-I',android,'--manifest',manifest,'--java',work/'gen','-o',work/'base.apk',work/'resources.zip')
java = list((source/'src').rglob('*.java')) + list((work/'gen').rglob('*.java'))
run(jdk/'bin/javac','-encoding','UTF-8','--release','8','-classpath',str(android)+os.pathsep+str(zxing),'-d',work/'classes',*java)
extra_jars = [options.cloud_libs/'verifier.jar', options.cloud_libs/'kotlin-stdlib.jar'] if options.cloud_libs else []
run(build_tools/'d8','--lib',android,'--min-api','26','--output',work/'dex',*list((work/'classes').rglob('*.class')),zxing,*extra_jars)
shutil.copy2(work/'base.apk',work/'unsigned.apk')
with zipfile.ZipFile(work/'unsigned.apk','a') as archive:
    if options.cloud_libs:
        archive.writestr('assets/cmb-cloud.enabled','v2')
        for name in ('libcmb_cloud.so','libcmb_cloud_android.so'):
            archive.write(options.cloud_libs/'arm64-v8a'/name, 'lib/arm64-v8a/'+name, compress_type=zipfile.ZIP_DEFLATED)
        archive.write(ROOT/'mobile/shared/cloud/LICENSE.upstream', 'assets/Bridge-Mobile-LICENSE.txt')
    for path in (work/'dex').glob('*.dex'):
        archive.write(path,path.name)
    for directory in (ROOT/'mobile/shared/web', source/'assets', ROOT/'web/client-icons'):
        for asset in directory.glob('*.js'):
            archive.write(asset,'assets/'+asset.name)
    for provider in ('codex', 'claude', 'deepseek', 'zcode'):
        archive.write(ROOT/'web/client-icons'/f'{provider}.png', 'assets/'+provider+'.png')
    archive.write(ROOT/'mobile/NOTICE.md','assets/NOTICE.md')
    archive.write(ROOT/'mobile/licenses/ZXing-LICENSE.txt','assets/ZXing-LICENSE.txt')
key = tools/'preview-debug.p12'
if not key.exists():
    run(jdk/'bin/keytool','-genkeypair','-keystore',key,'-storepass','android','-keypass','android','-alias','preview','-keyalg','RSA','-keysize','2048','-validity','3650','-dname','CN=Bridge Local Preview')
run(build_tools/'zipalign','-f','4',work/'unsigned.apk',work/'aligned.apk')
out = options.output_dir.resolve()
out.mkdir(parents=True,exist_ok=True)
version = json.loads((ROOT/'package.json').read_text())['version']
apk = out/('Bridge-Cloud-PR132-Test-arm64.apk' if options.cloud_libs else 'Codex-Mobile-Bridge-'+version+'-Android.apk')
run(build_tools/'apksigner','sign','--ks',key,'--ks-pass','pass:android','--out',apk,work/'aligned.apk')
run(build_tools/'apksigner','verify','--verbose',apk)
print(apk)
print('SHA-256',hashlib.sha256(apk.read_bytes()).hexdigest())
