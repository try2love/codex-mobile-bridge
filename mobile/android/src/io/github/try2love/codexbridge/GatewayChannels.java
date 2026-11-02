package io.github.try2love.codexbridge;

import android.content.Context;
import android.content.SharedPreferences;
import org.json.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.concurrent.*;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

/** One active address per authenticated gateway. Root credentials stay in the device vault. */
final class GatewayChannels {
 static String preferenceKey(SharedPreferences prefs,String name,String address){
  JSONObject record=record(prefs,address);return name+":"+(record==null?address:record.optString("deviceId"));
 }
 static String selectionId(String url){return url.startsWith("https://")&&url.endsWith(".trycloudflare.com")?"quick-tunnel":url;}
 static boolean enabled(SharedPreferences prefs,String computer,String url){
  return !prefs.getStringSet(preferenceKey(prefs,"disabled-channels",computer),Collections.emptySet()).contains(selectionId(url));
 }
 static String vault(String id){return "https://"+id+".route.invalid";}
 static String sign(String key,String message)throws Exception{
  Mac mac=Mac.getInstance("HmacSHA256");mac.init(new SecretKeySpec(key.getBytes(StandardCharsets.UTF_8),"HmacSHA256"));
  return ConnectionDiscovery.hex(mac.doFinal(message.getBytes(StandardCharsets.UTF_8)));
 }
 static JSONObject record(SharedPreferences prefs,String address){
  try{return new JSONObject(prefs.getString("channels:"+address,""));}catch(Exception ignored){return null;}
 }
 static String token(String root,String anchor,String target)throws Exception{
  return target.equals(anchor)?root:"cmbroute1_"+sign(root,"gateway-route-v1\n"+target);
 }
 static boolean proves(String device,String target,String secret)throws Exception{
  String key=ConnectionDiscovery.hash(secret),nonce=UUID.randomUUID().toString().replace("-","")+UUID.randomUUID().toString().replace("-","");
  JSONObject reply=new ConnectionDiscovery().read(target+"/api/connection/prove?device="+device+"&id="+ConnectionDiscovery.hash(key)+"&challenge="+nonce,null);
  return device.equals(reply.optString("deviceId"))&&java.security.MessageDigest.isEqual(sign(key,device+"\n"+target+"\n"+nonce).getBytes(StandardCharsets.UTF_8),reply.optString("proof").getBytes(StandardCharsets.UTF_8));
 }
 static List<String> endpoints(JSONObject descriptor)throws Exception{
  JSONArray array=descriptor.getJSONArray("endpoints");List<String> urls=new ArrayList<>();
  for(int i=0;i<array.length();i++){
   String target=array.optString(i);
   try{if(GatewayURL.origin(target).equals(target)&&target.equals(GatewayURL.httpOrigin(target))&&!urls.contains(target))urls.add(target);}catch(Exception unsupported){continue;}
  }return urls;
 }
 static JSONObject enroll(Context ctx,SharedPreferences prefs,String address,JSONObject descriptor,String name,String secret)throws Exception{
  if(descriptor==null||!descriptor.optString("deviceId").matches("[a-f0-9]{32}")||!endpoints(descriptor).contains(address))return null;
  String id=descriptor.getString("deviceId"),anchor=address;
  DeviceBinding.Credential root=DeviceBinding.load(ctx,vault(id));
  if(root!=null){
   anchor=prefs.getString("channel-anchor:"+id,address);
   if(!token(root.token,anchor,address).equals(secret)&&!proves(id,address,root.token))return null;
  }else if(secret.startsWith("cmbroute1_"))return null;
  return new JSONObject(descriptor.toString()).put("anchor",anchor);
 }
 interface Sample { void accept(String url, long millis); }
 // Platform I/O reports each completion as it arrives. The catalog supplies order and concurrency.
 private static final ExecutorService probePool=Executors.newFixedThreadPool(4);
 static void probe(JSONObject descriptor,String root,List<String> targets,Sample sample)throws Exception{
  String id=descriptor.getString("deviceId"),anchor=descriptor.getString("anchor");
  JSONObject policy=descriptor.optJSONObject("race");
  int limit=policy==null?4:Math.min(8,Math.max(1,policy.optInt("parallel",4)));
  if(targets.isEmpty())return;
  CompletionService<Runnable> done=new ExecutorCompletionService<>(probePool);
  List<Future<Runnable>> pending=new ArrayList<>();
  try{
   for(int i=0;i<targets.size();i++){
    if(i>=limit)done.take().get().run();
    String url=targets.get(i);pending.add(done.submit(()->{
    long began=System.nanoTime(),elapsed=-1;
    try{if(proves(id,url,token(root,anchor,url)))elapsed=(System.nanoTime()-began)/1000000;}catch(Exception unavailable){}
    final long ms=elapsed;return ()->sample.accept(url,ms);
   }));}
   for(int i=0;i<Math.min(limit,targets.size());i++)done.take().get().run();
  }finally{for(Future<?> f:pending)if(!f.isDone())f.cancel(true);}
 }
 static JSONObject refresh(JSONObject old,String reached,DeviceBinding.Credential root)throws Exception{
  String id=old.getString("deviceId"),anchor=old.getString("anchor");
  String cookie=root.name+"="+token(root.token,anchor,reached);
  ConnectionDiscovery http=new ConnectionDiscovery();
  JSONObject auth=http.request(reached+"/api/auth",null,cookie,null,null);
  JSONObject next=auth.getJSONObject("mobileRoutes");
  if(!auth.optBoolean("trustedDevice")||!id.equals(next.optString("deviceId")))throw new SecurityException();
  JSONArray missing=next.optJSONArray("unbound");
  if(missing!=null&&missing.length()>0){
   String verifier=ConnectionDiscovery.hash("anchor".equals(next.optString("renewal"))?"cmbroute1_"+sign(root.token,"gateway-route-v1\n"+anchor):sign(root.token,"gateway-route-renew-v1"));
   JSONObject claims=new JSONObject();List<String> allowed=endpoints(next);
   for(int i=0;i<missing.length();i++){
    String url=missing.getString(i);if(!allowed.contains(url))continue;
    String digest=ConnectionDiscovery.hash("cmbroute1_"+sign(root.token,"gateway-route-v1\n"+url));
    claims.put(url,new JSONObject().put("digest",digest).put("proof",sign(verifier,"route-grant-v1\n"+id+"\n"+url+"\n"+digest)));
    if(claims.length()==32||i+1==missing.length()){
     http.request(reached+"/api/connection/routes",null,cookie,auth.getString("csrf"),new JSONObject().put("deviceId",id).put("anchor",anchor).put("bindings",claims));
     claims=new JSONObject();
    }
   }
  }
  return next.put("anchor",anchor);
 }
}
