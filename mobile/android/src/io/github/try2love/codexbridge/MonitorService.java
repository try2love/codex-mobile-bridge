package io.github.try2love.codexbridge;
import android.app.*;
import android.content.*;
import android.os.*;
import org.json.*;
import java.util.concurrent.*;

/** Best effort while the process survives. This preview does not claim cloud push. */
public final class MonitorService extends Service {
 static volatile boolean visible;
 static volatile long foregroundEpoch;
 private static final Object foregroundLock=new Object();
 private static EventClient.Read activeRead;
 private static MonitorService service;
 private long epoch=-1;
 private final java.util.Set<String> baselined=new java.util.HashSet<>();
 private ScheduledExecutorService worker;
 private ScheduledFuture<?> scheduled;
 static void setVisible(boolean value){
  EventClient.Read pending;synchronized(foregroundLock){visible=value;foregroundEpoch++;pending=activeRead;activeRead=null;ComputerNotifications.stale();if(service!=null)service.schedule();}
  if(pending!=null)pending.cancel();
 }
 static void bindingsChanged(){ComputerNotifications.clear();setVisible(visible);}
 static void refresh(){synchronized(foregroundLock){if(service!=null&&visible)service.schedule();}}
 private void schedule(){if(scheduled!=null)scheduled.cancel(false);scheduled=visible?worker.scheduleWithFixedDelay(this::check,0,15,TimeUnit.SECONDS):null;}
 private boolean current(EventClient.Read pending,long ticket){return visible&&ticket==foregroundEpoch&&activeRead==pending;}
 public void onCreate(){super.onCreate();worker=Executors.newSingleThreadScheduledExecutor();synchronized(foregroundLock){service=this;schedule();}}
 private void check(){
  SharedPreferences p=getSharedPreferences("bridge",0);final long ticket;final EventClient.Read pending=new EventClient.Read();
  synchronized(foregroundLock){if(!visible||activeRead!=null)return;ticket=foregroundEpoch;activeRead=pending;if(epoch!=ticket){epoch=ticket;baselined.clear();}}
  try{for(String origin:new java.util.HashSet<>(p.getStringSet("origins",java.util.Collections.emptySet()))){
   synchronized(foregroundLock){if(!current(pending,ticket))return;if(!p.getStringSet("origins",java.util.Collections.emptySet()).contains(origin))continue;}
   String binding=EventClient.sessionCookie(android.webkit.CookieManager.getInstance().getCookie(origin+"/"));
   try{JSONObject data=EventClient.read(origin,Math.max(0,p.getLong("cursor:"+origin,0)),pending);
    synchronized(foregroundLock){if(!current(pending,ticket))return;if(!p.getStringSet("origins",java.util.Collections.emptySet()).contains(origin))continue;
    synchronized(EventClient.class){if(!binding.equals(EventClient.sessionCookie(android.webkit.CookieManager.getInstance().getCookie(origin+"/"))))continue;
    ComputerNotifications.update(origin,binding,data);if(!data.optBoolean("enabled"))continue;
    String key="cursor:"+origin,stream=data.getString("streamId");long previous=p.getLong(key,-1);
    boolean ready=baselined.contains(origin)&&stream.equals(p.getString("stream:"+origin,""));
    JSONArray events=data.getJSONArray("events");
    if(ready&&p.getBoolean("alerts",false)&&!p.getBoolean("pushRegistered:"+origin,false))for(int i=0;i<events.length();i++){
     JSONObject event=events.getJSONObject(i);if(!event.has("unread")||event.optBoolean("unread"))if(event.getLong("sequence")>Math.max(previous,p.getLong("cleared:"+origin+":"+stream,0)))notifyEvent(this,origin,event);
    }
    p.edit().putLong(key,data.getLong("cursor")).putString("stream:"+origin,stream).apply();baselined.add(origin);
    }}
   }catch(Exception ignored){synchronized(foregroundLock){if(current(pending,ticket))ComputerNotifications.forget(origin);}/* Keep the cursor; retry on the next foreground check. */}
  }}finally{synchronized(foregroundLock){if(activeRead==pending)activeRead=null;}}
 }

 static void notifyEvent(Context c,String origin,JSONObject event)throws Exception {
  notifyEvent(c,origin,event,"foreground-tasks");
 }
 static void notifyEvent(Context c,String origin,JSONObject event,String channel)throws Exception {
  NotificationManager manager=(NotificationManager)c.getSystemService(NOTIFICATION_SERVICE);
  createNotificationChannel(c,channel);
  int id=(origin+event.getString("id")).hashCode()&0x7fffffff;
  Intent intent=new Intent(c,MainActivity.class).setFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP|Intent.FLAG_ACTIVITY_SINGLE_TOP).putExtra("origin",origin).putExtra("thread",event.optString("threadId")).putExtra("host",event.optString("host","local"));
  PendingIntent tap=PendingIntent.getActivity(c,id,intent,PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);
  Bundle metadata=new Bundle();metadata.putString("bridgeOrigin",origin);metadata.putLong("bridgeSequence",event.optLong("sequence"));
  String provider=event.optString("provider");if(!java.util.Arrays.asList("codex","claude","deepseek").contains(provider))provider=event.optString("host").equals("desktop:claude")?"claude":event.optString("host").equals("desktop:deepseek")?"deepseek":"codex";
  Notification.Builder notification=new Notification.Builder(c,channel).setSmallIcon(c.getResources().getIdentifier("ic_notification","drawable",c.getPackageName())).addExtras(metadata).setContentTitle(event.getString("title")).setContentText(event.getString("body")).setSubText(provider.equals("claude")?"Claude":provider.equals("deepseek")?"DeepSeek Harness":"Codex").setVisibility(Notification.VISIBILITY_PRIVATE).setContentIntent(tap).setAutoCancel(true);
  try(java.io.InputStream image=c.getAssets().open(provider+".png")){notification.setLargeIcon(android.graphics.BitmapFactory.decodeStream(image));}catch(java.io.IOException ignored){/* Keep notification delivery if artwork is unavailable. */}
  manager.notify(id,notification.build());
 }
 static void createNotificationChannel(Context c,String id){
  NotificationManager manager=(NotificationManager)c.getSystemService(NOTIFICATION_SERVICE);
  // Existing channels belong to the user; never recreate them to undo a mute.
  if(manager.getNotificationChannel(id)!=null)return;
  NotificationChannel channel=new NotificationChannel(id,MobileStrings.text(c,"任务提醒"),NotificationManager.IMPORTANCE_HIGH);
  channel.setSound(android.provider.Settings.System.DEFAULT_NOTIFICATION_URI,new android.media.AudioAttributes.Builder().setUsage(android.media.AudioAttributes.USAGE_NOTIFICATION).setContentType(android.media.AudioAttributes.CONTENT_TYPE_SONIFICATION).build());
  manager.createNotificationChannel(channel);
 }
 public IBinder onBind(Intent i){return new Binder();}
 public void onDestroy(){EventClient.Read pending=null;synchronized(foregroundLock){if(scheduled!=null)scheduled.cancel(false);if(service==this){service=null;pending=activeRead;activeRead=null;}}if(pending!=null)pending.cancel();worker.shutdownNow();super.onDestroy();}
}
