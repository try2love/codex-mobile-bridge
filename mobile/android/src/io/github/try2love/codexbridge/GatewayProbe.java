package io.github.try2love.codexbridge;

import java.net.HttpURLConnection;
import java.net.URL;
import java.net.SocketTimeoutException;
import java.io.InputStream;
import java.io.ByteArrayOutputStream;
import org.json.JSONObject;

/** Unauthenticated reachability only: never follow a gateway redirect or send credentials. */
final class GatewayProbe {
 private volatile boolean cancelled;
 private volatile HttpURLConnection connection;
 void cancel(){cancelled=true;HttpURLConnection c=connection;if(c!=null)c.disconnect();}
 boolean check(String origin){
  long began=android.os.SystemClock.elapsedRealtime();
  Boolean quick=attempt(origin,began+4000);
  if(quick!=null)return quick;
  return !cancelled&&Boolean.TRUE.equals(attempt(origin,began+12000));
 }
 private static int remaining(long deadline)throws SocketTimeoutException {
  long value=deadline-android.os.SystemClock.elapsedRealtime();
  if(value<=0)throw new SocketTimeoutException();
  return (int)Math.min(Integer.MAX_VALUE,value);
 }
 // null means timeout only; invalid replies and explicit failures are final.
 private Boolean attempt(String origin,long deadline){
  HttpURLConnection c=null;
  try{
   if(cancelled)return false;
   c=(HttpURLConnection)new URL(GatewayURL.origin(origin)+"/api/auth").openConnection();connection=c;
   c.setConnectTimeout(remaining(deadline));c.setReadTimeout(remaining(deadline));c.setInstanceFollowRedirects(false);c.setUseCaches(false);
   c.setRequestProperty("Accept","application/json");
   if(cancelled)return false;
   c.connect();c.setReadTimeout(remaining(deadline));
   if(cancelled||c.getResponseCode()!=200||c.getContentLengthLong()>16384)return false;
   try(InputStream in=c.getInputStream();ByteArrayOutputStream out=new ByteArrayOutputStream()){
    byte[] buffer=new byte[2048];int n;
    while(true){
     if(cancelled)return false;c.setReadTimeout(remaining(deadline));n=in.read(buffer);if(n==-1)break;
     if(out.size()+n>16384)return false;remaining(deadline);out.write(buffer,0,n);
    }
    remaining(deadline);JSONObject data=new JSONObject(out.toString("UTF-8"));
    return !cancelled&&(!data.optBoolean("relay")||data.optBoolean("online"))&&data.opt("authenticated") instanceof Boolean&&data.opt("passwordless") instanceof Boolean&&data.opt("instanceId") instanceof String&&!data.getString("instanceId").isEmpty();
   }
  }catch(SocketTimeoutException timeout){return cancelled?false:null;}
  catch(Exception e){return false;}finally{connection=null;if(c!=null)c.disconnect();}
 }
}
