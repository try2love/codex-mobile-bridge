#!/usr/bin/env python3
"""Exercise the production Android notification presenter with an OS service fixture."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / '.tmp/android-task-notifications'
JAR = ROOT / '.tmp/mobile-tools/json-20240303.jar'
if not JAR.is_file():
    raise SystemExit('Run scripts/test-mobile-events.py first to prepare the pinned JSON fixture dependency.')
assert hashlib.sha256(JAR.read_bytes()).hexdigest() == '3cf6cd6892e32e2b4c1c39e0f52f5248a2f5b37646fdfbb79a66b46b618414ed'
STUBS = {
    'android/content/SharedPreferences.java': 'package android.content; public interface SharedPreferences {}',
    'android/content/Context.java': '''package android.content; public class Context {
 public static final String NOTIFICATION_SERVICE="notifications";
 public final android.app.NotificationManager manager=new android.app.NotificationManager();
 public org.json.JSONObject gateway; public boolean enabled=true;
 public Object getSystemService(String name){return manager;}
 public SharedPreferences getSharedPreferences(String name,int mode){return new Prefs(this);}
 public static class Prefs implements SharedPreferences {public final Context context; public Prefs(Context c){context=c;}}
 public android.content.res.Resources getResources(){return new android.content.res.Resources();}
 public android.content.res.AssetManager getAssets(){return new android.content.res.AssetManager();}
 public String getPackageName(){return "fixture";}
}''',
    'android/content/res/Resources.java': 'package android.content.res; public class Resources {public int getIdentifier(String a,String b,String c){return 1;}}',
    'android/content/res/AssetManager.java': 'package android.content.res; public class AssetManager {public java.io.InputStream open(String name)throws java.io.IOException{throw new java.io.IOException("no artwork in fixture");}}',
    'android/graphics/BitmapFactory.java': 'package android.graphics; public class BitmapFactory {public static Object decodeStream(java.io.InputStream stream){return null;}}',
    'android/content/Intent.java': '''package android.content; public class Intent {
 public static final int FLAG_ACTIVITY_CLEAR_TOP=1,FLAG_ACTIVITY_SINGLE_TOP=2;
 public android.net.Uri data; public Intent(Context c,Class<?> target){}
 public Intent setData(android.net.Uri d){data=d;return this;} public Intent setFlags(int flags){return this;}
}''',
    'android/net/Uri.java': '''package android.net; public class Uri {
 public final java.util.Map<String,String> params=new java.util.LinkedHashMap<>();
 public String toString(){return params.toString();}
 public static class Builder {Uri uri=new Uri();public Builder scheme(String s){return this;}public Builder authority(String a){return this;}
 public Builder appendQueryParameter(String key,String value){uri.params.put(key,value);return this;}public Uri build(){return uri;}}
}''',
    'android/os/Bundle.java': '''package android.os; public class Bundle {
 private final java.util.Map<String,Object> values=new java.util.HashMap<>();
 public void putString(String key,String value){values.put(key,value);}public void putLong(String key,long value){values.put(key,value);}
 public String getString(String key){return (String)values.get(key);}public long getLong(String key){return ((Number)values.get(key)).longValue();}
}''',
    'android/app/PendingIntent.java': '''package android.app; public class PendingIntent {
 public static final int FLAG_UPDATE_CURRENT=1,FLAG_IMMUTABLE=2;
 private static final java.util.Map<String,PendingIntent> cached=new java.util.HashMap<>();
 public android.content.Intent intent;
 public static PendingIntent getActivity(android.content.Context c,int id,android.content.Intent intent,int flags){
 String key=id+":"+intent.data;PendingIntent tap=cached.computeIfAbsent(key,k->new PendingIntent());tap.intent=intent;return tap;}
}''',
    'android/app/Notification.java': '''package android.app; public class Notification {
 public static final int VISIBILITY_PRIVATE=0; public android.os.Bundle extras;public PendingIntent tap;
 public String title,body;public boolean onlyAlertOnce,autoCancel;
 public static class Builder {private final Notification n=new Notification(); public Builder(android.content.Context c,String channel){}
 public Builder setSmallIcon(int id){return this;}public Builder addExtras(android.os.Bundle extra){n.extras=extra;return this;}
 public Builder setContentTitle(String title){n.title=title;return this;}public Builder setContentText(String body){n.body=body;return this;}
 public Builder setSubText(String source){return this;}public Builder setOnlyAlertOnce(boolean value){n.onlyAlertOnce=value;return this;}
 public Builder setVisibility(int value){return this;}public Builder setContentIntent(PendingIntent tap){n.tap=tap;return this;}
 public Builder setAutoCancel(boolean value){n.autoCancel=value;return this;}public Builder setLargeIcon(Object image){return this;}
 public Notification build(){return n;}}
}''',
    'android/service/notification/StatusBarNotification.java': '''package android.service.notification; public class StatusBarNotification {
 private final String tag;private final int id;private final android.app.Notification n;
 public StatusBarNotification(String tag,int id,android.app.Notification n){this.tag=tag;this.id=id;this.n=n;}
 public String getTag(){return tag;}public int getId(){return id;}public android.app.Notification getNotification(){return n;}
}''',
    'android/app/NotificationManager.java': '''package android.app; public class NotificationManager {
 public static final int IMPORTANCE_HIGH=4; public boolean enabled=true;
 private final java.util.Map<String,android.service.notification.StatusBarNotification> active=new java.util.LinkedHashMap<>();
 private final java.util.Map<String,NotificationChannel> channels=new java.util.HashMap<>();
 public boolean areNotificationsEnabled(){return enabled;}
 public NotificationChannel getNotificationChannel(String id){return channels.get(id);}public void createNotificationChannel(NotificationChannel channel){channels.put(channel.id,channel);}
 public android.service.notification.StatusBarNotification[] getActiveNotifications(){return active.values().toArray(new android.service.notification.StatusBarNotification[0]);}
 public void notify(String tag,int id,Notification n){active.put(tag+":"+id,new android.service.notification.StatusBarNotification(tag,id,n));}
 public void cancel(String tag,int id){active.remove(tag+":"+id);}
}''',
    'android/app/NotificationChannel.java': 'package android.app; public class NotificationChannel {public final String id;public NotificationChannel(String i,String name,int level){id=i;}public void setSound(Object uri,android.media.AudioAttributes audio){}}',
    'android/media/AudioAttributes.java': 'package android.media; public class AudioAttributes {public static final int USAGE_NOTIFICATION=5,CONTENT_TYPE_SONIFICATION=4;public static class Builder {public Builder setUsage(int x){return this;}public Builder setContentType(int x){return this;}public AudioAttributes build(){return new AudioAttributes();}}}',
    'android/provider/Settings.java': 'package android.provider; public class Settings {public static class System {public static final String DEFAULT_NOTIFICATION_URI="default";}}',
    'io/github/try2love/codexbridge/MainActivity.java': 'package io.github.try2love.codexbridge;final class MainActivity {}',
    'io/github/try2love/codexbridge/EventClient.java': 'package io.github.try2love.codexbridge;final class EventClient {}',
    'io/github/try2love/codexbridge/MobileStrings.java': 'package io.github.try2love.codexbridge;final class MobileStrings {static String text(android.content.Context c,String s){return s;}}',
    'io/github/try2love/codexbridge/GatewayChannels.java': 'package io.github.try2love.codexbridge;final class GatewayChannels {static org.json.JSONObject record(android.content.SharedPreferences p,String origin){return ((android.content.Context.Prefs)p).context.gateway;}}',
    'io/github/try2love/codexbridge/NotificationSources.java': 'package io.github.try2love.codexbridge;final class NotificationSources {static boolean enabled(android.content.SharedPreferences p,String origin){return ((android.content.Context.Prefs)p).context.enabled;}}',
}
for name, source in STUBS.items():
    path = WORK / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding='utf-8')
jdk = Path(os.environ['JAVA_HOME']) if os.environ.get('JAVA_HOME') else None
def tool(name):
    return str(jdk / 'bin' / name) if jdk else shutil.which(name)
subprocess.run([tool('javac'), '-encoding', 'UTF-8', '-cp', str(JAR), '-d', str(WORK),
                *[str(WORK / name) for name in STUBS],
                str(ROOT / 'mobile/android/src/io/github/try2love/codexbridge/TaskNotifications.java'),
                str(ROOT / 'mobile/tests/TaskNotificationTests.java')], check=True)
subprocess.run([tool('java'), '-cp', str(WORK) + os.pathsep + str(JAR),
                'io.github.try2love.codexbridge.TaskNotificationTests'], check=True)
