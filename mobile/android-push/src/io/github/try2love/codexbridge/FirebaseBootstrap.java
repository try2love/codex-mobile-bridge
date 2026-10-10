package io.github.try2love.codexbridge;
import android.app.*;
import android.content.*;
import com.google.firebase.FirebaseApp;
import com.google.firebase.messaging.FirebaseMessaging;
public final class FirebaseBootstrap {
 public static void enable(MainActivity app){
  if(FirebaseApp.initializeApp(app)==null){app.message("此安装包尚未配置 Firebase 项目");return;}
  MonitorService.createNotificationChannel(app,"tasks");
  FirebaseMessaging.getInstance().setAutoInitEnabled(true);
  FirebaseMessaging.getInstance().getToken().addOnSuccessListener(token->{app.getSharedPreferences("bridge",0).edit().putString("fcmToken",token).apply();app.registerPushToken();}).addOnFailureListener(e->app.message("系统推送注册失败，请检查 Google 服务与网络。"));
 }
}
