package io.github.try2love.codexbridge;
import android.webkit.CookieManager;
import org.json.*;
import java.net.*;
import java.io.*;
import java.nio.charset.StandardCharsets;

final class EventClient {
 static final String USER_AGENT="BridgeMobile/0.1-Android";
 static String sessionCookie(String header){if(header!=null)for(String part:header.split(";"))if(part.trim().startsWith("codex_mobile_session="))return part.trim();return "";}
 static synchronized void forget(String origin){CookieManager store=CookieManager.getInstance();store.setCookie(origin+"/","codex_mobile_session=; Path=/; Max-Age=0");store.flush();}
 static synchronized void renew(HttpURLConnection connection,String origin,String sent){
  CookieManager store=CookieManager.getInstance();String previous=sessionCookie(sent);
  if(previous.isEmpty()||!previous.equals(sessionCookie(store.getCookie(origin+"/"))))return;
  // Only renew the same authenticated binding. A late response must not
  // restore a removed connection or overwrite a newer sign-in.
  for(java.util.Map.Entry<String,java.util.List<String>> field:connection.getHeaderFields().entrySet())if("Set-Cookie".equalsIgnoreCase(field.getKey()))for(String value:field.getValue()){
   if(!previous.equals(value.split(";",2)[0].trim()))continue;
   try{java.util.List<HttpCookie> parsed=HttpCookie.parse(value);if(parsed.size()!=1||parsed.get(0).getDomain()!=null||!"/".equals(parsed.get(0).getPath()))continue;}catch(IllegalArgumentException invalid){continue;}
   store.setCookie(origin+"/",value);store.flush();return;
  }
 }
 static JSONObject read(String origin)throws Exception {
  if(!GatewayURL.origin(origin).equals(origin))throw new IOException("连接地址无效");
  String cookie=CookieManager.getInstance().getCookie(origin+"/");if(cookie==null||cookie.isEmpty())throw new IOException("请先登录电脑网关");
  HttpURLConnection c=(HttpURLConnection)new URL(origin+"/api/mobile/events").openConnection();
  c.setInstanceFollowRedirects(false);c.setConnectTimeout(7000);c.setReadTimeout(10000);c.setRequestProperty("Cookie",cookie);c.setRequestProperty("Origin",origin);c.setRequestProperty("User-Agent",USER_AGENT);
  try{int code=c.getResponseCode();if(code==401||code==403)throw new IOException("登录已失效，请重新连接电脑");if(code==404)throw new IOException("请使用配套的电脑端 Preview 网关");if(code!=200)throw new IOException("暂时无法读取提醒，请检查连接");
   renew(c,origin,cookie);
   ByteArrayOutputStream out=new ByteArrayOutputStream();try(InputStream in=c.getInputStream()){byte[] b=new byte[8192];int n;while((n=in.read(b))!=-1){if(out.size()+n>512000)throw new IOException("通知数据过大");out.write(b,0,n);}}
   return new JSONObject(out.toString("UTF-8"));
  }finally{c.disconnect();}
 }
 static void register(android.content.Context context,String token)throws Exception {
  android.content.SharedPreferences prefs=context.getSharedPreferences("bridge",0);
  if(!prefs.getBoolean("nativePushEnabled",false))return;
  String origin=prefs.getString("active","");if(origin.isEmpty())return;
  JSONObject auth=request(origin,"/api/auth",null,null);if(!auth.optBoolean("authenticated"))return;
  String id=prefs.getString("pushDeviceID",null);if(id==null){id=java.util.UUID.randomUUID().toString();prefs.edit().putString("pushDeviceID",id).apply();}
  request(origin,"/api/mobile/push",new JSONObject().put("kind","fcm").put("token",token).put("deviceId",id),auth.getString("csrf"));
  prefs.edit().putBoolean("pushRegistered:"+origin,true).apply();
 }
 static JSONObject request(String origin,String path,JSONObject body,String csrf)throws Exception {
  if(!GatewayURL.origin(origin).equals(origin))throw new IOException("连接地址无效");
  HttpURLConnection c=(HttpURLConnection)new URL(origin+path).openConnection();c.setInstanceFollowRedirects(false);c.setConnectTimeout(7000);c.setReadTimeout(10000);
  String cookie=CookieManager.getInstance().getCookie(origin+"/");if(cookie!=null)c.setRequestProperty("Cookie",cookie);c.setRequestProperty("Origin",origin);c.setRequestProperty("User-Agent",USER_AGENT);
  try{if(body!=null){c.setRequestMethod("POST");c.setDoOutput(true);c.setRequestProperty("Content-Type","application/json");c.setRequestProperty("X-CSRF-Token",csrf);try(OutputStream out=c.getOutputStream()){out.write(body.toString().getBytes(StandardCharsets.UTF_8));}}
   if(c.getResponseCode()!=200)throw new IOException("电脑未配置系统推送，或登录已失效");
   renew(c,origin,cookie);
   ByteArrayOutputStream out=new ByteArrayOutputStream();try(InputStream in=c.getInputStream()){byte[] b=new byte[4096];int n;while((n=in.read(b))!=-1){if(out.size()+n>65536)throw new IOException("响应过大");out.write(b,0,n);}}return new JSONObject(out.toString("UTF-8"));
  }finally{c.disconnect();}
 }

}
