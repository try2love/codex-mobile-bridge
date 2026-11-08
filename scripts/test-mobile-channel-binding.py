#!/usr/bin/env python3
"""Exercise the shipped Android route enrollment against a cookie-free proof server."""
import os
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]
WORK=ROOT/'.tmp/mobile-channel-binding'
WORK.mkdir(parents=True,exist_ok=True)
files={
'android/content/Context.java':'package android.content; public class Context {}',
'android/content/SharedPreferences.java':'''package android.content; import java.util.*; public interface SharedPreferences {String getString(String k,String d);Set<String> getStringSet(String k,Set<String> d);Map<String,?> getAll();}''',
'org/json/JSONArray.java':'''package org.json; import java.util.*; public class JSONArray {final List<String> data=new ArrayList<>();public JSONArray put(String s){data.add(s);return this;}public int length(){return data.size();}public String getString(int i){return data.get(i);}public String optString(int i){return data.get(i);}}''',
'org/json/JSONObject.java':'''package org.json; import java.util.*; public class JSONObject {static final Map<String,JSONObject> wire=new HashMap<>();final Map<String,Object> data=new HashMap<>();public JSONObject(){}public JSONObject(String s){data.putAll(wire.get(s).data);}public JSONObject put(String k,Object v){data.put(k,v);return this;}public String getString(String k){return (String)data.get(k);}public String optString(String k){return optString(k,"");}public String optString(String k,String d){return data.get(k) instanceof String?(String)data.get(k):d;}public int optInt(String k,int d){return data.get(k) instanceof Integer?(Integer)data.get(k):d;}public boolean optBoolean(String k){return Boolean.TRUE.equals(data.get(k));}public JSONObject getJSONObject(String k){return (JSONObject)data.get(k);}public JSONObject optJSONObject(String k){return (JSONObject)data.get(k);}public JSONArray getJSONArray(String k){return (JSONArray)data.get(k);}public JSONArray optJSONArray(String k){return (JSONArray)data.get(k);}public int length(){return data.size();}public String toString(){String id="fixture-"+UUID.randomUUID();wire.put(id,this);return id;}}''',
'io/github/try2love/codexbridge/DeviceBinding.java':'''package io.github.try2love.codexbridge;import android.content.Context;final class DeviceBinding {static Credential root;static final java.util.Map<String,Credential> aliases=new java.util.HashMap<>();static final class Credential{final String name,token;Credential(String n,String t){name=n;token=t;}}static Credential load(Context c,String key){return key.endsWith(".route.invalid")?root:aliases.get(key);}static boolean name(String n){return n.matches("codex_mobile_session_[a-f0-9]{24}");}}''',
'io/github/try2love/codexbridge/EventClient.java':'''package io.github.try2love.codexbridge;final class EventClient{static final String USER_AGENT="BridgeMobile/fixture";}''',
'io/github/try2love/codexbridge/ChannelBindingTests.java':r'''package io.github.try2love.codexbridge;
import java.net.*;import java.io.*;import java.nio.charset.StandardCharsets;import java.util.*;import android.content.*;import org.json.*;
public class ChannelBindingTests {
 static final String NAME="codex_mobile_session_"+"1".repeat(24),ID="a".repeat(32),ADDRESS="https://same.example",OTHER="http://192.168.1.1:8788",ROOT="r".repeat(64),NEW="n".repeat(64);
 static final Set<String> active=new HashSet<>();static boolean forged;static int calls;
 static class Prefs implements SharedPreferences {final Map<String,String> values=new HashMap<>();public String getString(String k,String d){return values.getOrDefault(k,d);}public Set<String> getStringSet(String k,Set<String> d){return d;}public Map<String,?> getAll(){return values;}}
 static void check(boolean ok,String msg){if(!ok)throw new AssertionError(msg);}
 static class Reply extends HttpURLConnection {
  byte[] body;Reply(URL u){super(u);}public void connect(){}public void disconnect(){}public boolean usingProxy(){return false;}
  public int getResponseCode()throws IOException{
   calls++;check(getRequestProperty("Cookie")==null&&!getInstanceFollowRedirects(),"proofs carry no credentials or redirects");
   Map<String,String> q=new HashMap<>();for(String pair:url.getQuery().split("&")){String[] v=pair.split("=");q.put(v[0],v[1]);}
   try {String found=null;for(String s:active)if(ConnectionDiscovery.hash(ConnectionDiscovery.hash(s)).equals(q.get("id")))found=s;
    if(found==null)return 403;String target=url.getProtocol()+"://"+url.getAuthority();
    body=new JSONObject().put("deviceId",forged?"b".repeat(32):ID).put("proof",GatewayChannels.sign(ConnectionDiscovery.hash(found),ID+"\n"+target+"\n"+q.get("challenge"))).toString().getBytes(StandardCharsets.UTF_8);return 200;
   }catch(Exception e){throw new IOException(e);}
  }
  public long getContentLengthLong(){return body==null?0:body.length;}public InputStream getInputStream(){return new ByteArrayInputStream(body);}
 }
 public static void main(String[] args)throws Exception {
  URL.setURLStreamHandlerFactory(p->new URLStreamHandler(){protected URLConnection openConnection(URL u){return new Reply(u);}});
  Context context=new Context();Prefs prefs=new Prefs();prefs.values.put("channel-anchor:"+ID,ADDRESS);
  JSONObject descriptor=new JSONObject().put("deviceId",ID).put("endpoints",new JSONArray().put(ADDRESS).put(OTHER));
  prefs.values.put("channels:"+ADDRESS,descriptor.toString());
  DeviceBinding.root=new DeviceBinding.Credential(NAME,ROOT);active.add(NEW);
  GatewayChannels.Enrollment fresh=GatewayChannels.enroll(context,prefs,OTHER,descriptor,NAME,NEW);
  check(fresh!=null,"fresh trusted login must replace a revoked root using proof of the fresh binding");
  check(fresh.root.token.equals(NEW)&&fresh.previous.token.equals(ROOT)&&fresh.descriptor.getString("anchor").equals(OTHER),"replacement carries the fresh root and anchor without mutating the vault before UI identity guards");
  int count=calls;String derived=GatewayChannels.token(ROOT,ADDRESS,OTHER);
  GatewayChannels.Enrollment retained=GatewayChannels.enroll(context,prefs,OTHER,descriptor,NAME,derived);
  check(retained.root.token.equals(ROOT)&&retained.descriptor.getString("anchor").equals(ADDRESS)&&calls==count,"a matching derived route keeps its proven root and anchor without another request");
  JSONObject outsider=new JSONObject().put("deviceId",ID).put("endpoints",new JSONArray().put("https://unrelated.example"));
  count=calls;check(GatewayChannels.enroll(context,prefs,"https://unrelated.example",outsider,NAME,NEW)==null&&calls==count,"a new origin cannot replace another computer by self-reporting its identity");
  forged=true;check(GatewayChannels.enroll(context,prefs,OTHER,descriptor,NAME,NEW)==null,"wrong identity cannot rebind");forged=false;
  active.clear();check(GatewayChannels.enroll(context,prefs,OTHER,descriptor,NAME,NEW)==null,"revoked new login cannot rebind");
  active.add(ROOT);check(GatewayChannels.enroll(context,prefs,OTHER,descriptor,NAME,"cmbroute1_"+"0".repeat(64))==null,"unrelated derived credential cannot become root");
  DeviceBinding.root=null;check(GatewayChannels.enroll(context,prefs,OTHER,descriptor,NAME,"cmbroute1_"+"0".repeat(64))==null,"a derived credential alone cannot bootstrap routes");
  prefs.values.remove("channels:"+ADDRESS);
  DeviceBinding.aliases.put(ADDRESS,new DeviceBinding.Credential(NAME,NEW));
  check(GatewayChannels.identity(context,prefs,ADDRESS).equals(ConnectionDiscovery.hash(NAME).substring(0,32)),"legacy saved aliases recover the authenticated instance identity");
  DeviceBinding.aliases.put(OTHER,new DeviceBinding.Credential("codex_mobile_session_"+"2".repeat(24),NEW));
  check(!GatewayChannels.identity(context,prefs,ADDRESS).equals(GatewayChannels.identity(context,prefs,OTHER)),"another gateway namespace cannot merge even on the same computer");
  DeviceBinding.aliases.put(OTHER,new DeviceBinding.Credential("codex_mobile_session",NEW));
  check(GatewayChannels.identity(context,prefs,OTHER).isEmpty(),"unscoped legacy names cannot establish identity");
  prefs.values.put("channels:"+OTHER,descriptor.toString());
  check(GatewayChannels.identity(context,prefs,OTHER).equals(ID),"a proven descriptor remains the authoritative identity");
  System.out.println("Native route binding: renewed login, revoked root, wrong identity, revoked fresh login and unrelated derived token passed");
 }
}'''
}
for name,source in files.items():
 p=WORK/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(source)
jdk=Path(os.environ['JAVA_HOME'])/'bin'
src=ROOT/'mobile/android/src/io/github/try2love/codexbridge'
subprocess.run([str(jdk/'javac'),'-d',str(WORK),*[str(src/name) for name in ['GatewayURL.java','ConnectionDiscovery.java','GatewayChannels.java']],*[str(WORK/name) for name in files]],check=True)
subprocess.run([str(jdk/'java'),'-cp',str(WORK),'io.github.try2love.codexbridge.ChannelBindingTests'],check=True,timeout=20)

# A real delayed loopback response exceeds the former 1.8/2-second budgets.
# No user gateway, model, account, cookies or external network participates.
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import hmac
import json
import threading
import time
from urllib.parse import parse_qs, urlsplit
fixture_secret='fixture-route-secret-'+'x'*48
fixture_id='a'*32
class Proof(BaseHTTPRequestHandler):
    delay=2.2
    forged=False
    def log_message(self,*_): pass
    def do_GET(self):
        assert self.headers.get('Cookie') is None and self.headers.get('Authorization') is None
        query=parse_qs(urlsplit(self.path).query)
        key=hashlib.sha256(fixture_secret.encode()).hexdigest()
        assert query['id']==[hashlib.sha256(key.encode()).hexdigest()]
        target='http://'+self.headers['Host']
        value=hmac.new(key.encode(),(fixture_id+'\n'+target+'\n'+query['challenge'][0]).encode(),hashlib.sha256).hexdigest()
        time.sleep(self.delay)
        body=json.dumps({'deviceId':'b'*32 if self.forged else fixture_id,'proof':value}).encode()
        self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
server=ThreadingHTTPServer(('127.0.0.1',0),Proof)
threading.Thread(target=server.serve_forever,daemon=True).start()
target='http://127.0.0.1:'+str(server.server_port)
try:
    java=WORK/'io/github/try2love/codexbridge/DelayedProbe.java'
    java.write_text('''package io.github.try2love.codexbridge;public class DelayedProbe {public static void main(String[] a)throws Exception {if(GatewayChannels.proves(a[0],a[1],a[2])!=Boolean.parseBoolean(a[3]))throw new AssertionError("probe identity/budget");}}''')
    subprocess.run([str(jdk/'javac'),'-cp',str(WORK),'-d',str(WORK),str(java)],check=True)
    swift=WORK/'probe.swift'
    swift.write_text('''import Foundation
@main struct Probe { static func main() async {let a=CommandLine.arguments;let result=await GatewayChannels.proves(a[1],target:a[2],token:a[3]);precondition(result == (a[4]=="true"))}}''')
    subprocess.run(['xcrun','swiftc','-parse-as-library','-module-cache-path',str(WORK/'swift-cache'),str(ROOT/'mobile/ios/BridgePreview/GatewayURL.swift'),str(ROOT/'mobile/ios/BridgePreview/ConnectionDiscovery.swift'),str(swift),'-o',str(WORK/'probe')],check=True)
    json_jar=Path(os.environ.get('JSON_JAR',str(ROOT/'.tmp/mobile-tools/json-20240303.jar')))
    if not json_jar.is_file(): raise RuntimeError('Set JSON_JAR to the existing org.json test dependency')
    for command in [[str(jdk/'java'),'-cp',str(json_jar)+os.pathsep+str(WORK),'io.github.try2love.codexbridge.DelayedProbe'],[str(WORK/'probe')]]:
        Proof.delay=2.2;Proof.forged=False
        subprocess.run(command+[fixture_id,target,fixture_secret,'true'],check=True,timeout=15)
        Proof.delay=0;Proof.forged=True
        subprocess.run(command+[fixture_id,target,fixture_secret,'false'],check=True,timeout=15)
    print('Android and iOS production probes accepted a 2.2-second delayed response and rejected a forged identity')
finally:
    server.shutdown();server.server_close()
