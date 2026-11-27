package io.github.try2love.codexbridge;
import android.webkit.CookieManager;
import org.json.*;
import java.net.*;
import java.io.*;
import java.nio.charset.StandardCharsets;

final class EventClient {
 static final String USER_AGENT="BridgeMobile/0.1-Android";
 static final int POLL_SECONDS=60;
 static final class AuthenticationRequired extends IOException {
  AuthenticationRequired(){super("登录已失效，请重新连接电脑");}
 }
 static final class HttpFailure extends IOException {
  final int status;
  HttpFailure(int status){super("Gateway returned HTTP "+status);this.status=status;}
 }
 private static final java.util.Map<String,String> names=new java.util.concurrent.ConcurrentHashMap<>();
 static void setName(String origin,String name){if(name!=null&&name.matches("codex_mobile_session_[a-f0-9]{24}|codex_mobile_session"))names.put(origin,name);}
 static String cookieName(String origin){return names.getOrDefault(origin,"codex_mobile_session");}
 static final class Read {
  private volatile boolean cancelled;
  private HttpURLConnection connection;
  synchronized void attach(HttpURLConnection value)throws IOException {check();connection=value;}
  synchronized void detach(){connection=null;}
  void check()throws IOException {if(cancelled)throw new InterruptedIOException("Notification check cancelled");}
  void cancel(){HttpURLConnection current;synchronized(this){cancelled=true;current=connection;connection=null;}if(current!=null)current.disconnect();}
 }
 static String sessionCookie(String header,String origin){String wanted=cookieName(origin)+"=";if(header!=null)for(String part:header.split(";"))if(part.trim().startsWith(wanted))return part.trim();return "";}
 static String tokenValue(String cookie){return cookie.isEmpty()?"":cookie.substring(cookie.indexOf('=')+1);}
 static synchronized void forget(String origin){CookieManager store=CookieManager.getInstance();store.setCookie(origin+"/",cookieName(origin)+"=; Path="+GatewayURL.cookiePath(origin)+"; Max-Age=0");store.flush();names.remove(origin);}
 static synchronized void renew(HttpURLConnection connection,String origin,String sent){
  CookieManager store=CookieManager.getInstance();String previous=sessionCookie(sent,origin);
  if(previous.isEmpty()||!previous.equals(sessionCookie(store.getCookie(origin+"/"),origin)))return;
  // Only renew the same authenticated binding. A late response must not
  // restore a removed connection or overwrite a newer sign-in.
  for(java.util.Map.Entry<String,java.util.List<String>> field:connection.getHeaderFields().entrySet())if("Set-Cookie".equalsIgnoreCase(field.getKey()))for(String value:field.getValue()){
   String replacement=value.split(";",2)[0].trim();
   if(!previous.equals(replacement)&&!(previous.startsWith("codex_mobile_session=")&&replacement.startsWith("codex_mobile_session_")&&replacement.endsWith(previous.substring(previous.indexOf('=')))))continue;
   try{java.util.List<HttpCookie> parsed=HttpCookie.parse(value);if(parsed.size()!=1||parsed.get(0).getDomain()!=null||!GatewayURL.cookiePath(origin).equals(parsed.get(0).getPath()))continue;}catch(IllegalArgumentException invalid){continue;}
   store.setCookie(origin+"/",value);store.flush();if(replacement.startsWith("codex_mobile_session_"))setName(origin,replacement.substring(0,replacement.indexOf('=')));return;
  }
 }
 static JSONObject read(String origin)throws Exception {
  return read(origin,null,null);
 }
 static JSONObject read(String origin,Long after,Read pending)throws Exception {
  return read(origin,after,pending,null,null);
 }
 static JSONObject read(String origin,Long after,Read pending,String stream,String revision)throws Exception {
  if(!GatewayURL.origin(origin).equals(origin))throw new IOException("连接地址无效");
  String cookie=CookieManager.getInstance().getCookie(origin+"/"),binding=sessionCookie(cookie,origin);if(binding.isEmpty())throw new AuthenticationRequired();
  boolean waiting=after!=null&&stream!=null&&revision!=null;
  String query=after==null?"":"?after="+Math.max(0,after);
  if(waiting)query+="&wait="+POLL_SECONDS+"&streamId="+URLEncoder.encode(stream,"UTF-8")+"&revision="+URLEncoder.encode(revision,"UTF-8");
  HttpURLConnection c=(HttpURLConnection)new URL(origin+"/api/mobile/events"+query).openConnection();
  c.setInstanceFollowRedirects(false);c.setConnectTimeout(7000);c.setReadTimeout(waiting?80000:10000);c.setRequestProperty("Cookie",cookie);c.setRequestProperty("Origin",GatewayURL.httpOrigin(origin));c.setRequestProperty("User-Agent",USER_AGENT);
  try{if(pending!=null)pending.attach(c);int code=c.getResponseCode();if(code==401)throw new AuthenticationRequired();if(code!=200)throw new HttpFailure(code);
   if(pending!=null)pending.check();
   renew(c,origin,cookie);
   ByteArrayOutputStream out=new ByteArrayOutputStream();try(InputStream in=c.getInputStream()){byte[] b=new byte[8192];int n;while((n=in.read(b))!=-1){if(pending!=null)pending.check();if(out.size()+n>512000)throw new IOException("通知数据过大");out.write(b,0,n);}}
   if(pending!=null){pending.check();if(!tokenValue(binding).equals(tokenValue(sessionCookie(CookieManager.getInstance().getCookie(origin+"/"),origin))))throw new IOException("登录已失效，请重新连接电脑");}
   return new JSONObject(out.toString("UTF-8"));
  }finally{if(pending!=null)pending.detach();c.disconnect();}
 }
 static void register(android.content.Context context,String token)throws Exception {
  android.content.SharedPreferences prefs=context.getSharedPreferences("bridge",0);
  if(!prefs.getBoolean("nativePushEnabled",false))return;
  String origin=prefs.getString("active","");if(origin.isEmpty())return;
  JSONObject auth=request(origin,"/api/auth",null,null);if(!auth.optBoolean("authenticated"))return;
  String id=prefs.getString("pushDeviceID",null);if(id==null){id=java.util.UUID.randomUUID().toString();prefs.edit().putString("pushDeviceID",id).apply();}
  request(origin,"/api/mobile/push",new JSONObject().put("kind","fcm").put("token",token).put("deviceId",id).put("sourceIcon",true),auth.getString("csrf"));
  prefs.edit().putBoolean("pushRegistered:"+origin,true).apply();
 }
 static final class OfflineVerification {
  final JSONObject value;
  final String rejection;
  OfflineVerification(JSONObject value,String rejection){this.value=value;this.rejection=rejection;}
 }
 static OfflineVerification verifyOffline(String origin,String path,String cookie)throws Exception {
  if(!GatewayURL.origin(origin).equals(origin)||cookie==null||cookie.isEmpty())throw new IOException("Invalid binding");
  HttpURLConnection c=(HttpURLConnection)new URL(origin+path).openConnection();
  c.setInstanceFollowRedirects(false);c.setConnectTimeout(7000);c.setReadTimeout(10000);
  c.setRequestProperty("Cookie",cookie);c.setRequestProperty("Origin",GatewayURL.httpOrigin(origin));c.setRequestProperty("User-Agent",USER_AGENT);
  try{
   int code=c.getResponseCode();
   if(code!=200&&code!=401&&code!=403)throw new HttpFailure(code);
   String type=c.getContentType();
   if(type==null||!"application/json".equalsIgnoreCase(type.split(";",2)[0].trim()))throw new HttpFailure(code);
   ByteArrayOutputStream out=new ByteArrayOutputStream();
   try(InputStream in=code==200?c.getInputStream():c.getErrorStream()){
    if(in==null)throw new HttpFailure(code);
    byte[] block=new byte[4096];int n;
    while((n=in.read(block))!=-1){if(out.size()+n>65536)throw new IOException("响应过大");out.write(block,0,n);}
   }
   JSONObject message=new JSONObject(out.toString("UTF-8"));
   if(code==200&&(!"/api/auth".equals(path)||!Boolean.FALSE.equals(message.opt("authenticated"))))
    return new OfflineVerification(message,null);
   String status=message.optString("authStatus");
   if(status.equals("expired")||status.equals("revoked")||status.equals("loggedOut")||status.equals("unknownRejected"))
    return new OfflineVerification(null,status);
   if(code==401&&(Boolean.FALSE.equals(message.opt("authenticated"))||"unauthenticated".equals(message.optString("code")))
       ||code==200&&Boolean.FALSE.equals(message.opt("authenticated")))
    return new OfflineVerification(null,"unknownRejected");
   throw new HttpFailure(code);
  }finally{c.disconnect();}
 }
 static JSONObject request(String origin,String path,JSONObject body,String csrf)throws Exception {
  if(!GatewayURL.origin(origin).equals(origin))throw new IOException("连接地址无效");
  HttpURLConnection c=(HttpURLConnection)new URL(origin+path).openConnection();c.setInstanceFollowRedirects(false);c.setConnectTimeout(7000);c.setReadTimeout(10000);
  String cookie=CookieManager.getInstance().getCookie(origin+"/");if(cookie!=null)c.setRequestProperty("Cookie",cookie);c.setRequestProperty("Origin",GatewayURL.httpOrigin(origin));c.setRequestProperty("User-Agent",USER_AGENT);
  try{if(body!=null){c.setRequestMethod("POST");c.setDoOutput(true);c.setRequestProperty("Content-Type","application/json");c.setRequestProperty("X-CSRF-Token",csrf);try(OutputStream out=c.getOutputStream()){out.write(body.toString().getBytes(StandardCharsets.UTF_8));}}
   if(c.getResponseCode()!=200)throw new IOException("电脑未配置系统推送，或登录已失效");
   renew(c,origin,cookie);
   ByteArrayOutputStream out=new ByteArrayOutputStream();try(InputStream in=c.getInputStream()){byte[] b=new byte[4096];int n;while((n=in.read(b))!=-1){if(out.size()+n>65536)throw new IOException("响应过大");out.write(b,0,n);}}JSONObject result=new JSONObject(out.toString("UTF-8"));if("/api/auth".equals(path))setName(origin,result.optString("cookieName",null));return result;
  }finally{c.disconnect();}
 }

}
