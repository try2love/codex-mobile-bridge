package io.github.abyxs.codexmobile;

import android.app.*;
import android.content.*;
import android.os.*;
import android.webkit.CookieManager;
import org.json.*;
import java.net.*;
import java.io.*;
import java.util.*;
import java.util.concurrent.*;

public class TaskNotificationService extends Service {
    public static volatile boolean visible = false;
    private final Map<String, JSONObject> previous = new ConcurrentHashMap<>();
    private final ScheduledExecutorService worker = Executors.newSingleThreadScheduledExecutor();
    private volatile String origin;
    private volatile boolean stopped;
    private boolean scheduled;
    private NotificationManager manager;
    private static final String MONITOR = "task-monitor", EVENTS = "task-events";
    @Override public void onCreate() {
        super.onCreate();
        manager = (NotificationManager)getSystemService(NOTIFICATION_SERVICE);
        manager.createNotificationChannel(new NotificationChannel(MONITOR,"后台连接",NotificationManager.IMPORTANCE_LOW));
        manager.createNotificationChannel(new NotificationChannel(EVENTS,"任务结果",NotificationManager.IMPORTANCE_DEFAULT));
    }
    private PendingIntent open() {
        return PendingIntent.getActivity(this,0,new Intent(this,MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP|Intent.FLAG_ACTIVITY_CLEAR_TOP),PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);
    }
    private Notification notice(String channel,String title,String text) {
        return new Notification.Builder(this,channel).setSmallIcon(R.mipmap.ic_launcher).setContentTitle(title)
            .setContentText(text).setStyle(new Notification.BigTextStyle().bigText(text)).setContentIntent(open())
            .setVisibility(Notification.VISIBILITY_PRIVATE).setAutoCancel(!channel.equals(MONITOR)).build();
    }
    @Override public int onStartCommand(Intent intent,int flags,int startId) {
        if(intent==null||intent.getStringExtra("origin")==null){stopSelf();return START_NOT_STICKY;}
        String value=intent.getStringExtra("origin");
        try { URI uri=new URI(value); if(!"https".equals(uri.getScheme())||uri.getHost()==null||uri.getUserInfo()!=null||uri.getQuery()!=null||uri.getFragment()!=null)throw new Exception(); }
        catch(Exception error){stopSelf();return START_NOT_STICKY;}
        startForeground(71,notice(MONITOR,"任务通知已开启","保持连接中；省电限制或网络中断可能延迟通知"));
        if(!value.equals(origin)){origin=value;worker.execute(()->previous.clear());}
        if(!scheduled){scheduled=true;worker.scheduleWithFixedDelay(this::poll,0,15,TimeUnit.SECONDS);}
        return START_NOT_STICKY;
    }
    private void poll() {
        String base=origin;
        if(stopped||base==null)return;
        HttpURLConnection connection=null;
        try {
            String cookie=CookieManager.getInstance().getCookie(base);
            connection=(HttpURLConnection)new URL(base+"api/task-status").openConnection();
            connection.setInstanceFollowRedirects(false);connection.setConnectTimeout(10000);connection.setReadTimeout(20000);
            if(cookie!=null)connection.setRequestProperty("Cookie",cookie);
            int code=connection.getResponseCode();
            if(stopped||!base.equals(origin))return;
            if(code==401||code==403){disable("登录已失效，请打开助手登录后重新开启通知");return;}
            if(code!=200)throw new IOException();
            ByteArrayOutputStream bytes=new ByteArrayOutputStream();
            try(InputStream stream=connection.getInputStream()){byte[] buffer=new byte[8192];int n;while((n=stream.read(buffer))!=-1){if(bytes.size()+n>4*1024*1024)throw new IOException();bytes.write(buffer,0,n);}}
            JSONArray tasks=new JSONObject(bytes.toString("UTF-8")).getJSONArray("tasks");
            if(stopped||!base.equals(origin))return;
            for(int i=0;i<tasks.length();i++){
                JSONObject task=tasks.getJSONObject(i);String key=task.getString("host")+"|"+task.getString("id");
                JSONObject prior=previous.put(key,task);if(prior==null)continue;
                String requests=task.optJSONArray("requests").toString(),message=null;
                if(!requests.equals("[]")&&!requests.equals(prior.optJSONArray("requests").toString()))message="需要你的确认";
                else if(prior.optString("status").equals("active")&&task.optString("status").equals("idle")&&requests.equals("[]"))message=task.optBoolean("error")?"任务结束，请查看错误":"任务已完成";
                if(message!=null&&!visible)manager.notify(1000+(key.hashCode()&0x3fffffff),notice(EVENTS,task.optString("title","对话"),message));
            }
            manager.notify(71,notice(MONITOR,"任务通知已开启","正在跟踪电脑任务，可在助手连接菜单关闭"));
        }catch(Exception error){if(!stopped)manager.notify(71,notice(MONITOR,"等待恢复连接","网络恢复后继续检查；请保持电脑和助手运行"));}
        finally{if(connection!=null)connection.disconnect();}
    }
    private void disable(String reason){
        stopped=true;
        getSharedPreferences("connection",MODE_PRIVATE).edit().putBoolean("task_notifications",false).apply();
        manager.notify(72,notice(EVENTS,"后台通知已停止",reason));stopSelf();
    }
    @Override public void onTimeout(int startId,int fgsType){disable("系统后台运行时限已到，请打开助手后重新开启");}
    @Override public void onDestroy(){stopped=true;origin=null;worker.shutdownNow();previous.clear();stopForeground(STOP_FOREGROUND_REMOVE);super.onDestroy();}
    @Override public IBinder onBind(Intent intent){return null;}
}
