"""Real frozen Mac peers and release hook; fixture key, no real client/config."""
PROBE = r'''
"""Disposable frozen test entry; never included in a release package."""
import json
import socket
import sys
from pathlib import Path
from bridge.platforms.macos import release_identity
from bridge.platforms.macos.signed_peer import SignedGatewayPeer

mode, key, endpoint = sys.argv[1:4]
# Only this disposable test executable trusts the temporary fixture publisher.
release_identity.public_key_bytes = lambda: Path(key).read_bytes()
signature = SignedGatewayPeer()
app = Path(sys.executable).resolve().parents[3]
signature.verify_starter(app / 'Contents/Helpers/CMBZCodePipeStarter.app')
if mode == 'server':
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(endpoint)
        server.listen(1)
        print('ready', flush=True)
        with server.accept()[0] as channel:
            channel.settimeout(8)
            pid = int(channel.recv(32))
            signature.require(channel, pid)
            channel.sendall(b'ok')
elif mode == 'client':
    with socket.socket(socket.AF_UNIX) as channel:
        channel.settimeout(8)
        channel.connect(endpoint)
        moved = []
        signature.require(channel, int(sys.argv[4]), relocated_holder=moved.append)
        channel.sendall(str(__import__('os').getpid()).encode())
        assert channel.recv(2) == b'ok'
        print(json.dumps({'mutualAuthentication': True, 'relocatedHolderVerified': bool(moved)}))
else:
    raise ValueError(mode)
'''
from pathlib import Path
import json, os, plistlib, shutil, subprocess, sys, tempfile
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
source=Path(__file__).resolve().parents[2]
if sys.platform != 'darwin':
 raise RuntimeError('This native acceptance requires macOS')
(source/'.tmp').mkdir(exist_ok=True)
workspace=tempfile.TemporaryDirectory(prefix='zcode-published-peer-',dir=source/'.tmp')
root=Path(workspace.name)
entry=root/'probe.py';entry.write_text(PROBE)
subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--onedir',
 '--name','codex-mobile-gateway','--paths',str(source),'--distpath',str(root/'dist'),
 '--workpath',str(root/'build'),'--specpath',str(root),str(entry)],check=True)
fixture=root/'hook-source';fixture.mkdir(exist_ok=True)
for relative in ['scripts/sign-zcode-launcher.cjs','scripts/zcode_starter_bundle.py','bridge/platforms/macos/zcode-pipe-starter.m']:
 p=fixture/relative;p.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source/relative,p)
key=Ed25519PrivateKey.generate();private=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()).decode();public=key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo)
pub=fixture/'desktop/features/updates/update-public-key.pem';pub.parent.mkdir(parents=True,exist_ok=True);pub.write_bytes(public)
app=root/'Current.app';previous=root/'Previous.app';socket='test.sock'
for p in (app,previous):
 if p.exists():shutil.rmtree(p)
(app/'Contents/Resources').mkdir(parents=True)
shutil.copytree(root/'dist/codex-mobile-gateway',app/'Contents/Resources/gateway',symlinks=True)
(app/'Contents/MacOS').mkdir()
shutil.copyfile('/usr/bin/true',app/'Contents/MacOS/Bridge')
(app/'Contents/MacOS/Bridge').chmod(0o755)
(app/'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':'io.github.try2love.codexmobilebridge','CFBundleExecutable':'Bridge','CFBundleName':'Bridge','CFBundlePackageType':'APPL','CFBundleVersion':'1'}))
(app/'Contents/Resources/update-version.json').write_text(json.dumps({'version':'2.2.0'}))
node="const hook=require(process.argv[1]);hook({electronPlatformName:'darwin',appOutDir:process.argv[2],packager:{appInfo:{id:'io.github.try2love.codexmobilebridge',productFilename:'Current',version:'2.2.0'},platformSpecificBuildOptions:{identity:'-'}}}).catch(e=>{console.error(e.message);process.exitCode=1});"
def finish():
 result=subprocess.run(['node','-e',node,str(fixture/'scripts/sign-zcode-launcher.cjs'),str(root)],env={**os.environ,'CMB_UPDATE_SIGNING_KEY':private},capture_output=True,text=True)
 if result.returncode:raise RuntimeError(result.stderr)
def probe(relocate=False):
 endpoint=root/socket
 if endpoint.exists():endpoint.unlink()
 exe=app/'Contents/Resources/gateway/codex-mobile-gateway'
 server=subprocess.Popen([str(exe),'server',str(pub),socket],cwd=root,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
 try:
  line=server.stdout.readline().strip()
  if line!='ready':raise RuntimeError('server failed: '+server.stderr.read())
  if relocate:
   app.rename(previous);shutil.copytree(previous,app,symlinks=True)
   shutil.rmtree(app/'Contents/Helpers');(app/'Contents/Resources/zcode-pipe-identity.json').unlink()
   subprocess.run(['/usr/bin/codesign','--force','--sign','-','--identifier','codex-mobile-gateway','--options','kill',str(exe)],check=True,capture_output=True)
   finish()
  result=subprocess.run([str(exe),'client',str(pub),socket,str(server.pid)],cwd=root,text=True,capture_output=True,timeout=20)
  if result.returncode:raise RuntimeError('client failed: '+result.stderr)
  output=json.loads(result.stdout);assert output['mutualAuthentication'];assert output['relocatedHolderVerified']==relocate,output
  _,err=server.communicate(timeout=10);assert server.returncode==0,err
  print(json.dumps({'relocation':relocate,**output}),flush=True)
  return output
 finally:
  if server.poll() is None:server.terminate();server.wait(timeout=5)
  endpoint.unlink(missing_ok=True)
finish()
results=[probe(),probe(True)]
print('PASS published production peers, native launcher, and different-build handoff')
workspace.cleanup()
