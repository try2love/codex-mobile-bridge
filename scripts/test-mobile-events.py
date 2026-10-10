#!/usr/bin/env python3
"""Run real Android event HTTP and polling code against isolated local fixtures."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / '.tmp/mobile-events-tests'
WORK.mkdir(parents=True, exist_ok=True)
parser = argparse.ArgumentParser()
parser.add_argument('--baseline', action='store_true')
args = parser.parse_args()
# Only OS delivery/storage and the JSON container are substituted. JSON objects
# register their serialized body in this single-process fixture; production
# EventClient still downloads the actual UTF-8 payload over loopback HTTP.
stubs = {
'android/webkit/CookieManager.java': r'''package android.webkit;
public class CookieManager {
 private static final CookieManager instance=new CookieManager();
 public volatile String value="codex_mobile_session=fixture";public Runnable afterRead;public int reads,switchAt;
 public static CookieManager getInstance(){return instance;}
 public String getCookie(String url){String snapshot=value;if(++reads==switchAt&&afterRead!=null)afterRead.run();return snapshot;}
 public void setCookie(String url,String header){value=header.split(";",2)[0];}
 public void flush(){}
}''',
'android/content/Context.java': r'''package android.content;
public abstract class Context {public abstract SharedPreferences getSharedPreferences(String name,int mode);}''',
'android/content/SharedPreferences.java': r'''package android.content;
import java.util.*;
public interface SharedPreferences {boolean getBoolean(String k,boolean d);String getString(String k,String d);long getLong(String k,long d);Set<String> getStringSet(String k,Set<String> d);Editor edit();interface Editor{Editor putString(String k,String v);Editor putBoolean(String k,boolean v);Editor putLong(String k,long v);void apply();}}''',
'io/github/try2love/codexbridge/MobileStrings.java': 'package io.github.try2love.codexbridge;final class MobileStrings {static String text(android.content.Context c,String s){return s;}}',
'android/content/Intent.java': 'package android.content;public class Intent {}',
'android/app/Service.java': r'''package android.app;
public class Service extends android.content.Context {public android.content.SharedPreferences preferences;public android.content.SharedPreferences getSharedPreferences(String n,int m){return preferences;}public void onCreate(){}public void onDestroy(){}}''',
'android/os/IBinder.java': 'package android.os;public interface IBinder {}',
'android/os/Binder.java': 'package android.os;public class Binder implements IBinder {}',
'org/json/JSONObject.java': r'''package org.json;
import java.util.*;
public class JSONObject {
 private static final Map<String,JSONObject> wire=new java.util.concurrent.ConcurrentHashMap<>();
 private final Map<String,Object> data=new LinkedHashMap<>();
 public JSONObject(){} public JSONObject(String s){JSONObject found=wire.get(s);if(found==null)throw new IllegalArgumentException("Unknown fixture JSON");data.putAll(found.data);}
 public boolean has(String k){return data.containsKey(k);}
 public JSONObject put(String k,Object v){data.put(k,v);return this;}
 public String getString(String k){return (String)data.get(k);}public long getLong(String k){return ((Number)data.get(k)).longValue();}
 public JSONArray getJSONArray(String k){return (JSONArray)data.get(k);}public boolean optBoolean(String k){return Boolean.TRUE.equals(data.get(k));}
 public String optString(String k){return data.get(k) instanceof String?(String)data.get(k):"";}public long optLong(String k){return data.get(k) instanceof Number?((Number)data.get(k)).longValue():0;}public JSONArray optJSONArray(String k){return (JSONArray)data.get(k);}
 public String toString(){StringJoiner out=new StringJoiner(", ","{","}");for(Map.Entry<String,Object> e:data.entrySet())out.add(encode(e.getKey())+": "+encode(e.getValue()));String s=out.toString();wire.put(s,this);return s;}
 static String encode(Object o){return o instanceof String ? "\""+((String)o).replace("\\","\\\\").replace("\"","\\\"").replace("\n","\\n")+"\"" : String.valueOf(o);}
}''',
'org/json/JSONArray.java': r'''package org.json;
import java.util.*;
public class JSONArray {private final List<JSONObject> data=new ArrayList<>();public JSONArray put(JSONObject v){data.add(v);return this;}public int length(){return data.size();}public JSONObject getJSONObject(int i){return data.get(i);}public JSONObject optJSONObject(int i){return i<data.size()?data.get(i):null;}public String toString(){StringJoiner out=new StringJoiner(", ","[","]");for(JSONObject o:data)out.add(o.toString());return out.toString();}}''',
}
for name, source in stubs.items():
    path = WORK / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source)
source = (ROOT / 'mobile/android/src/io/github/try2love/codexbridge/MonitorService.java').read_text()
# Keep the production scheduler/check/lifecycle unchanged. OS notification
# rendering is replaced at its existing method boundary to record delivery.
start = source.index(' static void notifyEvent(')
end = source.index(' public IBinder onBind', start)
source = source[:start] + ' static void notifyEvent(Context c,String origin,JSONObject event)throws Exception { MobileEventTests.delivered.add(origin+":"+event.getLong("sequence")); }\n' + source[end:]
(WORK / 'MonitorService.java').write_text(source)
java_home = os.environ.get('JAVA_HOME')
def java_tool(name):
    return str(Path(java_home) / 'bin' / name) if java_home else shutil.which(name)
subprocess.run([java_tool('javac'), '-d', str(WORK),
               *[str(WORK / name) for name in stubs], str(WORK / 'MonitorService.java'),
               str(ROOT / 'mobile/android/src/io/github/try2love/codexbridge/GatewayURL.java'),
               str(ROOT / 'mobile/android/src/io/github/try2love/codexbridge/EventClient.java'),
               str(ROOT / 'mobile/android/src/io/github/try2love/codexbridge/ComputerNotifications.java'),
               str(ROOT / 'mobile/tests/MobileEventTests.java')], check=True)
subprocess.run([java_tool('java'), '-Dsun.net.http.allowRestrictedHeaders=true', '-cp', str(WORK), 'io.github.try2love.codexbridge.MobileEventTests', 'baseline' if args.baseline else 'regression'], check=True)
