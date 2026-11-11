#!/usr/bin/env python3
"""Prepare or run exact Android moveChannel with JDK-only isolated callbacks."""
import argparse
import ast
import os
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1];WORK=ROOT/'.tmp/android-channel-handoff-tests'
parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--prepare-only',action='store_true');args=parser.parse_args()
# Reuse the existing enrollment fixture's JSON stand-ins; never run its unrelated proof suite.
old=ast.parse((ROOT/'scripts/test-mobile-channel-binding.py').read_text())
files=ast.literal_eval(next(node.value for node in old.body if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='files' for t in node.targets)))
files={key:value for key,value in files.items() if key.startswith('org/json/')}
files['org/json/JSONObject.java']=files['org/json/JSONObject.java'].replace('public class JSONObject {',
 r'''public class JSONObject {public static String quote(String value){return "\""+value.replace("\\","\\\\").replace("\"","\\\"")+"\"";}''')
files['android/content/Context.java']='package android.content; public class Context {}'
files['android/content/SharedPreferences.java']='''package android.content;import java.util.*;public interface SharedPreferences {String getString(String k,String d);Set<String> getStringSet(String k,Set<String>d);Map<String,?> getAll();Editor edit();interface Editor{Editor putString(String k,String v);Editor putStringSet(String k,Set<String>v);Editor putBoolean(String k,boolean v);void apply();}}'''
files['android/widget/Toast.java']='''package android.widget;public class Toast{public static final int LENGTH_SHORT=0;public static Toast makeText(Object c,String s,int n){return new Toast();}public void show(){}}'''
files['android/webkit/CookieManager.java']=r'''package android.webkit;import java.net.*;import java.util.*;import java.util.function.Consumer;public class CookieManager{
 static final CookieManager INSTANCE=new CookieManager();static java.net.CookieManager jar;static final List<Runnable> callbacks=new ArrayList<>();public static int writes;public static Runnable afterSet;
 public static CookieManager getInstance(){return INSTANCE;}public static void reset(){jar=new java.net.CookieManager(null,CookiePolicy.ACCEPT_ALL);callbacks.clear();writes=0;afterSet=null;}
 public static void seed(String url,String value){try{jar.put(new URI(url),Map.of("Set-Cookie",List.of(value)));}catch(Exception e){throw new AssertionError(e);}}
 public String getCookie(String url){try{List<String> values=new ArrayList<>();for(String header:jar.get(new URI(url),Map.of()).getOrDefault("Cookie",List.of()))for(String part:header.split(";")){String item=part.trim();if(item.startsWith("$"))continue;int equal=item.indexOf('=');if(equal<0)continue;String value=item.substring(equal+1);if(value.startsWith("\"")&&value.endsWith("\""))value=value.substring(1,value.length()-1);values.add(item.substring(0,equal+1)+value);}return String.join("; ",values);}catch(Exception e){throw new AssertionError(e);}}
 public void setCookie(String url,String value){writes++;seed(url,value);}public void setCookie(String url,String value,Consumer<Boolean> callback){setCookie(url,value);callbacks.add(()->{Runnable hook=afterSet;afterSet=null;if(hook!=null)hook.run();callback.accept(true);});}
 public static void drain(){while(!callbacks.isEmpty())callbacks.remove(0).run();}
}'''
base='io/github/try2love/codexbridge/'
files[base+'DeviceBinding.java']='''package io.github.try2love.codexbridge;import android.content.Context;import java.util.*;final class DeviceBinding {static final Map<String,Credential> values=new HashMap<>();static final class Credential{final String name,token;Credential(String n,String t){name=n;token=t;}String cookie(){return name+"="+token;}}static Credential load(Context c,String key){return values.get(key);}static void save(Context c,String key,String name,String token){values.put(key,new Credential(name,token));}static boolean name(String n){return n.matches("codex_mobile_session_[a-f0-9]{24}");}}'''
files[base+'EventClient.java']='''package io.github.try2love.codexbridge;import java.util.*;final class EventClient{static final String USER_AGENT="BridgeMobile/isolated-fixture";static final Map<String,String>names=new HashMap<>();static String cookieName(String a){return names.getOrDefault(a,"codex_mobile_session");}static void setName(String a,String n){names.put(a,n);}static String sessionCookie(String h,String a){return BindingSelection.cookie(h,cookieName(a));}static String tokenValue(String c){return c.isEmpty()?"":c.substring(c.indexOf('=')+1);}}'''
source=(ROOT/'mobile/android/src'/base/'MainActivity.java').read_text();template=(ROOT/'mobile/tests/ChannelMoveTests.java').read_text()
for marker,start,end in [('MOVE_CHANNEL',' private void moveChannel(',' private LinearLayout entryProgress;'),('PEER',' private static final class Peer{',' private Peer pendingPeer;')]:
 offset=source.index(start);template=template.replace(' // PRODUCTION_'+marker,source[offset:source.index(end,offset)].rstrip())
files[base+'ChannelMoveTests.java']=template
for name in ['GatewayURL.java','ConnectionDiscovery.java','GatewayChannels.java','BindingSelection.java']:
 files[base+name]=(ROOT/'mobile/android/src'/base/name).read_text()
for name,value in files.items():
 path=WORK/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(value)
if args.prepare_only:
 print('prepared exact Android methods; no Java process or network started');raise SystemExit(0)
jdk=Path(os.environ['JAVA_HOME'])/'bin';classes=WORK/'classes';classes.mkdir(exist_ok=True)
subprocess.run([str(jdk/'javac'),'-d',str(classes),*[str(WORK/name) for name in files]],check=True)
subprocess.run([str(jdk/'java'),'-cp',str(classes),'io.github.try2love.codexbridge.ChannelMoveTests'],check=True,timeout=20)
