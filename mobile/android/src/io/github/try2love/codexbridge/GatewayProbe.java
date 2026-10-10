package io.github.try2love.codexbridge;

import java.net.HttpURLConnection;
import java.net.URL;
import java.io.InputStream;
import java.io.ByteArrayOutputStream;
import org.json.JSONObject;

/** Unauthenticated reachability only: never follow a gateway redirect or send credentials. */
final class GatewayProbe {
 private volatile boolean cancelled;
 private volatile HttpURLConnection connection;
 void cancel(){cancelled=true;HttpURLConnection c=connection;if(c!=null)c.disconnect();}
 boolean check(String origin){
  HttpURLConnection c=null;
  try{
   if(cancelled)return false;
   c=(HttpURLConnection)new URL(GatewayURL.origin(origin)+"/api/auth").openConnection();connection=c;
   c.setConnectTimeout(4000);c.setReadTimeout(4000);c.setInstanceFollowRedirects(false);c.setUseCaches(false);
   c.setRequestProperty("Accept","application/json");
   if(cancelled||c.getResponseCode()!=200||c.getContentLengthLong()>16384)return false;
   try(InputStream in=c.getInputStream();ByteArrayOutputStream out=new ByteArrayOutputStream()){
    byte[] buffer=new byte[2048];int n;long deadline=android.os.SystemClock.elapsedRealtime()+4000;
    while((n=in.read(buffer))!=-1){if(cancelled||out.size()+n>16384||android.os.SystemClock.elapsedRealtime()>deadline)return false;out.write(buffer,0,n);}
    JSONObject data=new JSONObject(out.toString("UTF-8"));
    return !cancelled&&(!data.optBoolean("relay")||data.optBoolean("online"))&&data.opt("authenticated") instanceof Boolean&&data.opt("passwordless") instanceof Boolean&&data.opt("instanceId") instanceof String&&!data.getString("instanceId").isEmpty();
   }
  }catch(Exception e){return false;}finally{connection=null;if(c!=null)c.disconnect();}
 }
}
