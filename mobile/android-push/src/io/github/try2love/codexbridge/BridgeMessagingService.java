package io.github.try2love.codexbridge;
import com.google.firebase.messaging.FirebaseMessagingService;
import com.google.firebase.messaging.RemoteMessage;
public final class BridgeMessagingService extends FirebaseMessagingService {
 @Override public void onNewToken(String token){getSharedPreferences("bridge",0).edit().putString("fcmToken",token).apply();try{EventClient.register(getApplicationContext(),token);}catch(Exception ignored){/* Next foreground entry retries against the authenticated gateway. */}}
 @Override public void onMessageReceived(RemoteMessage message){
  // Foreground remains owned by the authenticated inbox. Background renders
  // bundled source icons without depending on another image download.
  if(MonitorService.visible)return;
  android.content.SharedPreferences prefs=getSharedPreferences("bridge",0);
  String origin=message.getData().get("origin");
  if(!prefs.getBoolean("nativePushEnabled",false)||origin==null||!prefs.getStringSet("origins",java.util.Collections.emptySet()).contains(origin))return;
  try{
   String thread=message.getData().get("thread"),host=message.getData().get("host");GatewayURL.chat(origin,thread,host);
   org.json.JSONObject event=new org.json.JSONObject(message.getData());
   event.put("threadId",thread).put("id","push:"+event.optString("streamId")+":"+event.optString("sequence"));
   MonitorService.notifyEvent(this,origin,event,"tasks");
  }catch(Exception ignored){/* Invalid or removed bindings never open a new computer. */}
 }
}
