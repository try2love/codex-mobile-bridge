#!/usr/bin/env python3
"""Run exact iOS handoff methods with an isolated simulator WKHTTPCookieStore."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import subprocess
import uuid

ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--device',default='FEB717DB-8605-4A29-B8C7-AB1CD574CEB4')
parser.add_argument('--compile-only',action='store_true')
parser.add_argument('--focus',choices=['full','remaining','seed-diagnostic','concurrent','ownership'],default='full')
args=parser.parse_args()
WORK=ROOT/'.tmp/ios-channel-binding-tests';WORK.mkdir(parents=True,exist_ok=True)
source=(ROOT/'mobile/ios/BridgePreview/App.swift').read_text()
template=(ROOT/'mobile/tests/ChannelBindingTests.swift').read_text()
for marker,start,end in [
 ('MAIN_COMMIT','    func webView(_ webView: WKWebView, didCommit navigation:', '    func webView(_ webView: WKWebView, didFinish navigation:'),
 ('COOKIE_SELECTION','    private func cookieName(','    private let page ='),
 ('GATEWAY_IDENTITY','    private func gatewayIdentity(','    private func enrollChannels('),
 ('MOVE_CHANNEL_AND_PEER','    private func moveChannel(','    private var routeScript='),
]:
 offset=source.index(start)
 template=template.replace('    // PRODUCTION_'+marker,source[offset:source.index(end,offset)].rstrip())
generated=WORK/'GeneratedChannelBindingTests.swift';generated.write_text(template)
app=WORK/'ChannelBindingTests.app';app.mkdir(exist_ok=True)
bundle='io.github.try2love.fixture.channel-binding'
run_id=str(uuid.uuid4())
with (app/'Info.plist').open('wb') as stream:
 plistlib.dump({'CFBundleIdentifier':bundle,'CFBundleExecutable':'ChannelBindingTests','CFBundlePackageType':'APPL',
  'CFBundleName':'Channel Binding Fixture','CFBundleVersion':'1','CFBundleShortVersionString':'1.0',
  'MinimumOSVersion':'17.0','LSRequiresIPhoneOS':True,'UIDeviceFamily':[1,2],
  'CFBundleSupportedPlatforms':['iPhoneSimulator']},stream)
sdk=subprocess.check_output(['xcrun','--sdk','iphonesimulator','--show-sdk-path'],text=True).strip()
subprocess.run(['xcrun','--sdk','iphonesimulator','swiftc','-swift-version','5','-parse-as-library','-target','arm64-apple-ios17.0-simulator',
 '-sdk',sdk,'-module-cache-path',str(WORK/'swift-cache'),str(ROOT/'mobile/ios/BridgePreview/GatewayURL.swift'),
 str(ROOT/'mobile/ios/BridgePreview/ConnectionDiscovery.swift'),str(generated),'-o',str(app/'ChannelBindingTests')],check=True,timeout=60)
if args.compile_only:
 print('compiled real WKHTTPCookieStore handoff fixture');raise SystemExit(0)
devices=json.loads(subprocess.check_output(['xcrun','simctl','list','devices','available','-j'],text=True))
selected=next((device for values in devices['devices'].values() for device in values if device['udid']==args.device),None)
if selected is None:raise ValueError('Requested installed simulator is unavailable')
booted=selected['state']=='Booted'
try:
 if not booted:
  subprocess.run(['xcrun','simctl','boot',args.device],check=True)
  subprocess.run(['xcrun','simctl','bootstatus',args.device,'-b'],check=True,timeout=60)
 subprocess.run(['xcrun','simctl','install',args.device,str(app)],check=True,timeout=30)
 subprocess.run(['xcrun','simctl','launch','--console-pty',args.device,bundle],check=True,timeout=60,env={**os.environ,'SIMCTL_CHILD_CHANNEL_BINDING_FOCUS':args.focus,'SIMCTL_CHILD_CHANNEL_BINDING_RUN_ID':run_id})
 container=Path(subprocess.check_output(['xcrun','simctl','get_app_container',args.device,bundle,'data'],text=True).strip())
 result=json.loads((container/'Documents/result.json').read_text())
 if result.get('runId')!=run_id or result.get('focus')!=args.focus:raise ValueError('Fixture receipt is not from this invocation')
 (WORK/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
 print(json.dumps(result,ensure_ascii=False))
 if result['status']!='passed':raise SystemExit(1)
finally:
 if not booted:subprocess.run(['xcrun','simctl','shutdown',args.device],check=True,timeout=30)
