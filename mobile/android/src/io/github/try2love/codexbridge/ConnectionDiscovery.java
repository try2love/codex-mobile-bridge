package io.github.try2love.codexbridge;

import java.net.HttpURLConnection;
import java.net.URL;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.UUID;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import org.json.*;

/** Address lookup is not authentication. Probe without cookies before relocating a binding. */
final class ConnectionDiscovery {
 static boolean retryableStatus(int status){return status==404||status==410||status>=500&&status<=599;}
 private volatile boolean cancelled;
 private volatile HttpURLConnection active;
 void cancel(){cancelled=true;HttpURLConnection c=active;if(c!=null)c.disconnect();}
 static String hex(byte[] bytes){StringBuilder s=new StringBuilder();for(byte b:bytes)s.append(String.format("%02x",b&255));return s.toString();}
 static String hash(String value)throws Exception{return hex(MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8)));}
 static String secret(String cookie){String value=EventClient.sessionCookie(cookie);return value.isEmpty()?"":value.substring(value.indexOf('=')+1);}
 static JSONObject descriptor(JSONObject auth,String cookie)throws Exception {
  JSONObject d=auth.optJSONObject("connection");String secret=secret(cookie);
  if(!auth.optBoolean("authenticated")||d==null||secret.isEmpty())return null;
  String relay=GatewayURL.origin(d.getString("relay"));
  if(!relay.equals(GatewayURL.httpOrigin(relay))||!relay.startsWith("https://")||!d.optString("deviceId").matches("[a-f0-9]{32}")||!d.optString("discoveryKey").matches("[a-f0-9]{64}"))return null;
  return new JSONObject(d.toString()).put("binding",hash(secret));
 }
 JSONObject read(String address,String discoveryKey)throws Exception {
  if(cancelled)throw new InterruptedIOException();
  HttpURLConnection c=(HttpURLConnection)new URL(address).openConnection();active=c;
  c.setInstanceFollowRedirects(false);c.setUseCaches(false);c.setConnectTimeout(1800);c.setReadTimeout(1800);c.setRequestProperty("Accept","application/json");
  if(discoveryKey!=null)c.setRequestProperty("X-Discovery-Key",discoveryKey);
  try{
   if(cancelled||c.getResponseCode()!=200||c.getContentLengthLong()>32768)throw new IOException();
   try(InputStream in=c.getInputStream();ByteArrayOutputStream out=new ByteArrayOutputStream()){
    byte[] bytes=new byte[2048];int n;long deadline=System.nanoTime()+2_000_000_000L;
    while((n=in.read(bytes))!=-1){if(cancelled||out.size()+n>32768||System.nanoTime()>deadline)throw new IOException();out.write(bytes,0,n);}
    return new JSONObject(out.toString("UTF-8"));
   }
  }finally{active=null;c.disconnect();}
 }
 String resolve(String old,JSONObject descriptor,String cookie){
  try{
   String secret=secret(cookie),key=hash(secret),device=descriptor.getString("deviceId");
   if(secret.isEmpty()||!key.equals(descriptor.optString("binding")))return old;
   JSONObject routes=read(descriptor.getString("relay")+"/relay/discover/"+device,descriptor.getString("discoveryKey"));
   if(!device.equals(routes.optString("deviceId")))return old;
   JSONArray endpoints=routes.getJSONArray("endpoints");
   for(int i=0;i<Math.min(16,endpoints.length())&&!cancelled;i++){
    String target;try{target=GatewayURL.origin(endpoints.getString(i));}catch(Exception e){continue;}
    // Never downgrade a saved binding or send a cookie to an unverified host.
    if(!target.startsWith("https://")||!target.equals(GatewayURL.httpOrigin(target)))continue;
    String nonce=UUID.randomUUID().toString().replace("-","")+UUID.randomUUID().toString().replace("-","");
    try{
     JSONObject proof=read(target+"/api/connection/prove?id="+hash(key)+"&challenge="+nonce,null);
     Mac mac=Mac.getInstance("HmacSHA256");mac.init(new SecretKeySpec(key.getBytes(StandardCharsets.UTF_8),"HmacSHA256"));
     String expected=hex(mac.doFinal((device+"\n"+target+"\n"+nonce).getBytes(StandardCharsets.UTF_8)));
     if(device.equals(proof.optString("deviceId"))&&MessageDigest.isEqual(expected.getBytes(StandardCharsets.US_ASCII),proof.optString("proof").getBytes(StandardCharsets.US_ASCII)))return target;
    }catch(Exception ignored){}
   }
  }catch(Exception ignored){}
  return old;
 }
}
