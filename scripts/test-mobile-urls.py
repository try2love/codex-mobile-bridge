#!/usr/bin/env python3
"""Run native URL boundary checks using the same cross-platform cases."""
import json
import subprocess
from pathlib import Path
root = Path(__file__).resolve().parents[1]
work = root/'.tmp/mobile-preview/url-tests'
work.mkdir(parents=True, exist_ok=True)
cases = json.loads((root/'mobile/tests/url-cases.json').read_text())
lines = ['import io.github.try2love.codexbridge.GatewayURL;', 'class URLTests { public static void main(String[] args)throws Exception {']
for value, expected in cases['accepted']:
    lines.append(f'if(!GatewayURL.connection({json.dumps(value)}).equals({json.dumps(expected)}))throw new AssertionError({json.dumps(value)});')
for value in cases['rejected']:
    lines.append(f'try{{GatewayURL.connection({json.dumps(value)});throw new AssertionError({json.dumps(value)});}}catch(Exception expected){{}}')
lines += [
    'String base="https://codex.try2love.com";',
    'if(!GatewayURL.sameOrigin(base+"/api/mobile/events",base))throw new AssertionError();',
    'for(String value:new String[]{"https://codex.try2love.com.evil.example/","http://codex.try2love.com/","https://user@codex.try2love.com/"})if(GatewayURL.sameOrigin(value,base))throw new AssertionError(value);',
    'if(!GatewayURL.chat(base,"aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee","my server/~").equals(base+"/#aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee~my%20server%2F%7E"))throw new AssertionError();',
    'try{GatewayURL.chat(base,"------------------------------------","local");throw new AssertionError();}catch(Exception expected){}',
    'System.out.println("Java: 28 URL and route checks passed");}}'
]
(work/'URLTests.java').write_text('\n'.join(lines))
jdk = next((root/'.tmp/mobile-tools/jdk').glob('*/Contents/Home'))
subprocess.run([str(jdk/'bin/javac'),'-d',str(work),str(root/'mobile/android/src/io/github/try2love/codexbridge/GatewayURL.java'),str(work/'URLTests.java')],check=True)
subprocess.run([str(jdk/'bin/java'),'-cp',str(work),'URLTests'],check=True)
subprocess.run(['xcrun','swiftc','-module-cache-path',str(work/'cache'),str(root/'mobile/ios/BridgePreview/GatewayURL.swift'),str(root/'mobile/tests/URLTests.swift'),'-o',str(work/'swift-tests')],check=True)
subprocess.run([str(work/'swift-tests'),str(root/'mobile/tests/url-cases.json')],check=True)
