#!/usr/bin/env python3
"""Exercise real Android background HTTP code with an in-memory WebView store."""
import os
from pathlib import Path
import shutil
import subprocess

root = Path(__file__).resolve().parents[1]
work = root / '.tmp/mobile-session-tests'
work.mkdir(parents=True, exist_ok=True)
# Only Android platform storage/context and JSON parsing are replaced; the
# production EventClient performs real HTTP against an isolated loopback server.
stubs = {
    'android/webkit/CookieManager.java': '''package android.webkit;
public class CookieManager {
 private static final CookieManager instance=new CookieManager();
 public volatile String value="codex_mobile_session=legacy-binding",lastSet="";public int flushes,reads;public Runnable afterRead;
 public static CookieManager getInstance(){return instance;}
 public String getCookie(String url){String snapshot=value;if(++reads==2&&afterRead!=null)afterRead.run();return snapshot;}
 public void setCookie(String url,String header){lastSet=header;value=header.split(";",2)[0];}
 public void flush(){flushes++;}
 public void reset(){value="codex_mobile_session=legacy-binding";lastSet="";flushes=0;reads=0;afterRead=null;}
}''',
    'android/content/Context.java': '''package android.content; public abstract class Context {public abstract SharedPreferences getSharedPreferences(String name,int mode);}''',
    'android/content/SharedPreferences.java': '''package android.content;public interface SharedPreferences {boolean getBoolean(String key,boolean fallback);String getString(String key,String fallback);Editor edit();interface Editor{Editor putString(String key,String value);Editor putBoolean(String key,boolean value);void apply();}}''',
    'org/json/JSONObject.java': '''package org.json; public class JSONObject {public JSONObject(){}public JSONObject(String value){}public boolean optBoolean(String key){return false;}public String getString(String key){return "";}public JSONObject put(String key,Object value){return this;}}''',
}
for name, source in stubs.items():
    path = work / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source)
java_home = os.environ.get('JAVA_HOME')
def java_tool(name):
    return str(Path(java_home) / 'bin' / name) if java_home else shutil.which(name)
subprocess.run([java_tool('javac'), '-d', str(work),
                *[str(work / name) for name in stubs],
                str(root / 'mobile/android/src/io/github/try2love/codexbridge/GatewayURL.java'),
                str(root / 'mobile/android/src/io/github/try2love/codexbridge/EventClient.java'),
                str(root / 'mobile/tests/EventClientTests.java')], check=True)
subprocess.run([java_tool('java'), '-cp', str(work), 'io.github.try2love.codexbridge.EventClientTests'], check=True)
