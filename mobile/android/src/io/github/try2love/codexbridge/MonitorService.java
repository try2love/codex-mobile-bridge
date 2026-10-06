package io.github.try2love.codexbridge;
import android.app.*;
import android.content.*;
import android.os.*;
import org.json.*;
import java.util.concurrent.*;

/** Best effort while the process survives. This preview does not claim cloud push. */
public final class MonitorService extends Service {
 static volatile boolean visible;
 private ScheduledExecutorService worker;
 public void onCreate(){super.onCreate();worker=Executors.newSingleThreadScheduledExecutor();worker.scheduleWithFixedDelay(this::check,0,15,TimeUnit.SECONDS);}
 private void check(){
  android.content.SharedPreferences p=getSharedPreferences("bridge",0);if(!p.getBoolean("alerts",false))return;
  String origin=p.getString("active","");if(origin.isEmpty())return;
  try{JSONObject data=EventClient.read(origin);if(!data.optBoolean("enabled"))return;
   String key="cursor:"+origin,stream=data.getString("streamId");long previous=p.getLong(key,-1);
   if(!stream.equals(p.getString("stream:"+origin,"")))previous=-1;
   JSONArray events=data.getJSONArray("events");if(previous>=0&&!visible&&!p.getBoolean("pushRegistered:"+origin,false)){for(int i=0;i<events.length();i++){JSONObject event=events.getJSONObject(i);if(event.getLong("sequence")>Math.max(previous,p.getLong("cleared:"+origin+":"+stream,0)))notifyEvent(this,origin,event);}}
   if(origin.equals(p.getString("active","")))p.edit().putLong(key,data.getLong("cursor")).putString("stream:"+origin,stream).apply();
  }catch(Exception ignored){/* Keep the cursor: the inbox is fetched again after reconnecting. */}
 }
 static void notifyEvent(Context c,String origin,JSONObject event)throws Exception {
  NotificationManager manager=(NotificationManager)c.getSystemService(NOTIFICATION_SERVICE);
  manager.createNotificationChannel(new NotificationChannel("tasks","任务提醒",NotificationManager.IMPORTANCE_DEFAULT));
  int id=(origin+event.getString("id")).hashCode()&0x7fffffff;
  Intent intent=new Intent(c,MainActivity.class).setFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP|Intent.FLAG_ACTIVITY_SINGLE_TOP).putExtra("origin",origin).putExtra("thread",event.optString("threadId")).putExtra("host",event.optString("host","local"));
  PendingIntent tap=PendingIntent.getActivity(c,id,intent,PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);
  Bundle metadata=new Bundle();metadata.putString("bridgeOrigin",origin);metadata.putLong("bridgeSequence",event.optLong("sequence"));
  manager.notify(id,new Notification.Builder(c,"tasks").setSmallIcon(c.getResources().getIdentifier("ic_bridge","drawable",c.getPackageName())).addExtras(metadata).setContentTitle(event.getString("title")).setContentText(event.getString("body")).setVisibility(Notification.VISIBILITY_PRIVATE).setContentIntent(tap).setAutoCancel(true).build());
 }
 public IBinder onBind(Intent i){return new Binder();}
 public void onDestroy(){worker.shutdownNow();super.onDestroy();}
}
