#!/usr/bin/env python3
"""Native discovery fixtures; fake transport, real URL/HMAC/cancellation code. No App build."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / '.tmp/mobile-discovery-tests'
WORK.mkdir(parents=True, exist_ok=True)
files = {
'org/json/JSONObject.java': '''package org.json; import java.util.*;
public class JSONObject {
 static final Map<String,JSONObject> wire=new HashMap<>();final Map<String,Object> data=new HashMap<>();
 public JSONObject(){} public JSONObject(String text){data.putAll(wire.get(text).data);}
 public JSONObject put(String key,Object value){data.put(key,value);return this;}
 public String getString(String key){return (String)data.get(key);}public String optString(String key){return data.get(key) instanceof String?(String)data.get(key):"";}
 public boolean optBoolean(String key){return Boolean.TRUE.equals(data.get(key));}
 public JSONObject optJSONObject(String key){return (JSONObject)data.get(key);}public JSONArray getJSONArray(String key){return (JSONArray)data.get(key);}
 public String toString(){String id="fixture-"+UUID.randomUUID();wire.put(id,this);return id;}
}''',
'org/json/JSONArray.java': '''package org.json; import java.util.*; public class JSONArray {List<String> rows=new ArrayList<>();public JSONArray put(String v){rows.add(v);return this;}public int length(){return rows.size();}public String getString(int i){return rows.get(i);}}''',
'io/github/try2love/codexbridge/EventClient.java': '''package io.github.try2love.codexbridge;final class EventClient {static String sessionCookie(String value){return value==null?"":value;}}''',
'io/github/try2love/codexbridge/DiscoveryTests.java': r'''package io.github.try2love.codexbridge;
import java.net.*;import java.io.*;import java.nio.charset.StandardCharsets;import java.util.*;import javax.crypto.Mac;import javax.crypto.spec.SecretKeySpec;import org.json.*;
public class DiscoveryTests {
 static final String DEVICE="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",SECRET="fixture-mobile-cookie",OLD="https://old.example",NEXT="https://next.example";
 static int calls;static boolean forged,redirect,badIdentity;
 static String sign(String target,String nonce)throws Exception{String key=ConnectionDiscovery.hash(SECRET);Mac mac=Mac.getInstance("HmacSHA256");mac.init(new SecretKeySpec(key.getBytes(StandardCharsets.UTF_8),"HmacSHA256"));return ConnectionDiscovery.hex(mac.doFinal((DEVICE+"\n"+target+"\n"+nonce).getBytes(StandardCharsets.UTF_8)));}
 static void check(boolean ok){if(!ok)throw new AssertionError();}
 static class Reply extends HttpURLConnection {
  byte[] body;
  Reply(URL u){super(u);}
  public void connect(){} public void disconnect(){}public boolean usingProxy(){return false;}
  public int getResponseCode()throws IOException {
   calls++;check(getRequestProperty("Cookie")==null&&getRequestProperty("Authorization")==null&&!getInstanceFollowRedirects());
   JSONObject value;
   if(url.getPath().startsWith("/relay/discover/")){
    check("b".repeat(64).equals(getRequestProperty("X-Discovery-Key")));
    value=new JSONObject().put("deviceId",badIdentity?"wrong":DEVICE).put("endpoints",new JSONArray().put("http://10.0.0.1").put("https://evil.example").put(NEXT));
   }else{
    check(getRequestProperty("X-Discovery-Key")==null);
    Map<String,String> q=new HashMap<>();for(String pair:url.getQuery().split("&")){String[] v=pair.split("=");q.put(v[0],v[1]);}
    try{check(q.get("id").equals(ConnectionDiscovery.hash(ConnectionDiscovery.hash(SECRET))));value=new JSONObject().put("deviceId",DEVICE).put("proof",forged||url.getHost().equals("evil.example")?"0".repeat(64):sign(NEXT,q.get("challenge")));}catch(Exception e){throw new IOException(e);}
   }
   body=value.toString().getBytes(StandardCharsets.UTF_8);return redirect?302:200;
  }
  public long getContentLengthLong(){return body.length;}public InputStream getInputStream(){return new ByteArrayInputStream(body);}
 }
 public static void main(String[] args)throws Exception {
  URL.setURLStreamHandlerFactory(protocol->protocol.equals("https")?new URLStreamHandler(){protected URLConnection openConnection(URL u){return new Reply(u);}}:null);
  for(int status:new int[]{404,410,502,503,504,530})check(ConnectionDiscovery.retryableStatus(status));
  for(int status:new int[]{200,302,401,403,429})check(!ConnectionDiscovery.retryableStatus(status));
  JSONObject auth=new JSONObject().put("authenticated",true).put("connection",new JSONObject().put("relay","https://relay.example").put("deviceId",DEVICE).put("discoveryKey","b".repeat(64)));
  String cookie="codex_mobile_session="+SECRET;JSONObject descriptor=ConnectionDiscovery.descriptor(auth,cookie);check(descriptor!=null);
  check(new ConnectionDiscovery().resolve(OLD,descriptor,cookie).equals(NEXT));check(calls==3);
  forged=true;check(new ConnectionDiscovery().resolve(OLD,descriptor,cookie).equals(OLD));forged=false;
  badIdentity=true;int before=calls;check(new ConnectionDiscovery().resolve(OLD,descriptor,cookie).equals(OLD));check(calls==before+1);badIdentity=false;
  redirect=true;before=calls;check(new ConnectionDiscovery().resolve(OLD,descriptor,cookie).equals(OLD));check(calls==before+1);redirect=false;
  before=calls;check(new ConnectionDiscovery().resolve(OLD,descriptor,"codex_mobile_session=changed").equals(OLD));check(calls==before);
  ConnectionDiscovery cancelled=new ConnectionDiscovery();cancelled.cancel();check(cancelled.resolve(OLD,descriptor,cookie).equals(OLD));check(calls==before);
  check(GatewayURL.targetBase(NEXT+"/?client=claude").equals(NEXT));check(GatewayURL.targetBase(NEXT+"/?client=deepseek").equals(NEXT));
  try{GatewayURL.connection(NEXT+"/?client=claude");throw new AssertionError();}catch(Exception expected){}
  String scope=NEXT+"/d/"+DEVICE;check(GatewayURL.httpOrigin(scope).equals(NEXT));check(GatewayURL.cookiePath(scope).equals("/d/"+DEVICE+"/"));check(GatewayURL.sameOrigin(scope+"/api/auth",scope));check(!GatewayURL.sameOrigin(NEXT+"/api/auth",scope));
  System.out.println("Android discovery: identity, forged origin, redirects, cookie-free probe, binding replacement, cancel, scoped URL and provider route checks passed");
 }
}'''
}
for name, source in files.items():
    path=WORK/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(source)
jdk=Path(os.environ['JAVA_HOME']) if os.environ.get('JAVA_HOME') else next((ROOT/'.tmp/mobile-tools/jdk').glob('*/Contents/Home'))
source=ROOT/'mobile/android/src/io/github/try2love/codexbridge'
subprocess.run([str(jdk/'bin/javac'),'-d',str(WORK),str(source/'GatewayURL.java'),str(source/'ConnectionDiscovery.java'),*[str(WORK/name) for name in files]],check=True)
subprocess.run([str(jdk/'bin/java'),'-cp',str(WORK),'io.github.try2love.codexbridge.DiscoveryTests'],check=True)
key=hashlib.sha256(b'fixture-mobile-cookie').hexdigest();device='a'*32;nonce='1'*64;target='https://next.example'
code=hmac.new(key.encode(),(device+'\n'+target+'\n'+nonce).encode(),hashlib.sha256).hexdigest()
swift='''import Foundation
@main struct Tests {static func main() {
 let key=KEY, device=DEVICE, target=TARGET, nonce=NONCE, code=CODE
 for status in [404,410,502,503,504,530] { precondition(ConnectionDiscovery.retryableStatus(status)) }
 for status in [200,302,401,403,429] { precondition(!ConnectionDiscovery.retryableStatus(status)) }
 precondition(ConnectionDiscovery.hash("fixture-mobile-cookie")==key)
 precondition(ConnectionDiscovery.verify(code,key:key,device:device,target:target,nonce:nonce))
 precondition(!ConnectionDiscovery.verify(code,key:key,device:device,target:"https://evil.example",nonce:nonce))
 precondition(!ConnectionDiscovery.verify(code,key:key,device:device,target:target,nonce:String(repeating:"2",count:64)))
 precondition(!ConnectionDiscovery.verify(String(repeating:"0",count:64),key:key,device:device,target:target,nonce:nonce))
 let scoped=target+"/d/"+device
 precondition(GatewayURL.cookiePath(scoped)=="/d/"+device+"/")
 precondition(GatewayURL.same(URL(string:scoped+"/api/auth")!,scoped))
 precondition(!GatewayURL.same(URL(string:target+"/api/auth")!,scoped))
 print("iOS discovery: backend HMAC vector, forged origin/nonce/proof and scoped URL checks passed")
}}'''
for name,value in [('KEY',key),('DEVICE',device),('TARGET',target),('NONCE',nonce),('CODE',code)]:swift=swift.replace(name,json.dumps(value))
(WORK/'main.swift').write_text(swift)
subprocess.run(['xcrun','swiftc','-parse-as-library','-module-cache-path',str(WORK/'cache'),str(ROOT/'mobile/ios/BridgePreview/GatewayURL.swift'),str(ROOT/'mobile/ios/BridgePreview/ConnectionDiscovery.swift'),str(WORK/'main.swift'),'-o',str(WORK/'swift-tests')],check=True)
subprocess.run([str(WORK/'swift-tests')],check=True)
