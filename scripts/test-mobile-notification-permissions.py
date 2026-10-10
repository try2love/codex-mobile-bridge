#!/usr/bin/env python3
"""Exercise native startup notification logic with isolated OS permission fixtures."""
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / '.tmp/mobile-notification-permissions'
WORK.mkdir(parents=True, exist_ok=True)


def section(source, start, end):
    return source[source.index(start):source.index(end, source.index(start))]


android = (ROOT / 'mobile/android/src/io/github/try2love/codexbridge/MainActivity.java').read_text()
monitor = (ROOT / 'mobile/android/src/io/github/try2love/codexbridge/MonitorService.java').read_text()
assert 'root.post(this::requestStartupNotifications)' in android
methods = section(android, ' void requestStartupNotifications()', ' void testNotification()')
methods += next(line for line in android.splitlines() if 'public void onRequestPermissionsResult(' in line)
channel = section(monitor, ' static void createNotificationChannel(', ' public IBinder onBind')
channel = channel.replace('android.media.AudioAttributes', 'AudioAttributes').replace('android.provider.Settings', 'Settings')
java = r'''
import java.util.*;
class Context {static final String NOTIFICATION_SERVICE="notifications";NotificationManager manager=new NotificationManager();Object getSystemService(String name){return manager;}}
class Activity extends Context {public void onRequestPermissionsResult(int code,String[] names,int[] results){}}
class Manifest {static class permission {static final String POST_NOTIFICATIONS="notifications";}}
class PackageManager {static final int PERMISSION_GRANTED=0;}
class Build {static class VERSION {static int SDK_INT=35;}}
class Settings {static class System {static final String DEFAULT_NOTIFICATION_URI="default-sound";}}
class AudioAttributes {static final int USAGE_NOTIFICATION=5,CONTENT_TYPE_SONIFICATION=4;int usage,type;static class Builder {AudioAttributes v=new AudioAttributes();Builder setUsage(int n){v.usage=n;return this;}Builder setContentType(int n){v.type=n;return this;}AudioAttributes build(){return v;}}}
class NotificationChannel {String id,sound;int importance;AudioAttributes audio;NotificationChannel(String i,String name,int level){id=i;importance=level;}void setSound(String uri,AudioAttributes attrs){sound=uri;audio=attrs;}}
class NotificationManager {static final int IMPORTANCE_HIGH=4;boolean enabled=true;int creates;Map<String,NotificationChannel> channels=new HashMap<>();boolean areNotificationsEnabled(){return enabled;}NotificationChannel getNotificationChannel(String id){return channels.get(id);}void createNotificationChannel(NotificationChannel value){creates++;channels.put(value.id,value);}}
class MobileStrings {static String text(Context c,String s){return s;}}
class Prefs {Map<String,Boolean> values=new HashMap<>();boolean contains(String k){return values.containsKey(k);}boolean getBoolean(String k,boolean d){return values.getOrDefault(k,d);}Prefs edit(){return this;}Prefs putBoolean(String k,boolean v){values.put(k,v);return this;}void apply(){}}
class MonitorService extends Context { CHANNEL }
class MainActivity extends Activity {
 Prefs prefs=new Prefs();int permission=-1,requests,code,monitors,other;List<String> messages=new ArrayList<>();
 int checkSelfPermission(String p){return permission;}
 void requestPermissions(String[] p,int c){requests++;code=c;}
 String L(String v){return v;}void message(String v){messages.add(v);}void monitor(){monitors++;}void enableNativePush(){other++;}void scan(){other++;}void testNotification(){other++;}
 METHODS
}
public class NotificationPermissions {
 static void check(boolean value,String message){if(!value)throw new AssertionError(message);}
 public static void main(String[] args){
  MainActivity fresh=new MainActivity();fresh.requestStartupNotifications();check(fresh.requests==1&&fresh.code==25,"first launch requests OS permission");check(!fresh.prefs.getBoolean("alerts",true),"wait for permission");fresh.requestStartupNotifications();check(fresh.requests==1,"no duplicate pending request");
  NotificationChannel c=fresh.manager.getNotificationChannel("foreground-tasks");check(c.importance==4&&"default-sound".equals(c.sound)&&c.audio.usage==5,"heads-up and notification sound defaults");c.importance=0;c.sound=null;fresh.requestStartupNotifications();check(fresh.manager.creates==1&&c.importance==0&&c.sound==null,"respect muted existing channel");
  fresh.onRequestPermissionsResult(25,new String[]{"notifications"},new int[]{0});check(fresh.prefs.getBoolean("alerts",false)&&fresh.monitors==1,"enable after grant");fresh.toggleNotifications();fresh.requestStartupNotifications();check(!fresh.prefs.getBoolean("alerts",true)&&fresh.requests==1,"explicit off survives restart");
  for(int[] result:new int[][]{new int[]{-1},new int[]{}}){MainActivity denied=new MainActivity();denied.requestStartupNotifications();denied.onRequestPermissionsResult(25,new String[]{"notifications"},result);denied.requestStartupNotifications();check(!denied.prefs.getBoolean("alerts",true)&&denied.requests==1&&denied.messages.isEmpty(),"deny or dismiss quietly without repeat");}
  for(boolean value:new boolean[]{false,true}){MainActivity saved=new MainActivity();saved.prefs.putBoolean("alerts",value);saved.requestStartupNotifications();check(saved.requests==0&&saved.prefs.getBoolean("alerts",!value)==value,"preserve existing preference");}
  MainActivity granted=new MainActivity();granted.permission=0;granted.requestStartupNotifications();check(granted.requests==0&&granted.prefs.getBoolean("alerts",false),"already authorized upgrade enables alerts");
  Build.VERSION.SDK_INT=32;MainActivity old=new MainActivity();old.requestStartupNotifications();check(old.requests==0&&old.prefs.getBoolean("alerts",false),"old Android enables without runtime prompt");MainActivity blocked=new MainActivity();blocked.manager.enabled=false;blocked.requestStartupNotifications();check(!blocked.prefs.getBoolean("alerts",true),"respect system block on old Android");
  Build.VERSION.SDK_INT=35;MainActivity manual=new MainActivity();manual.onRequestPermissionsResult(22,new String[]{"notifications"},new int[]{-1});check(manual.messages.get(0).equals("请在系统设置中允许通知。"),"notification denial must not show camera copy");
  System.out.println("PASS Android startup grant/deny/dismiss, upgrade, stored settings, channel sound and mute preservation");
 }
}
'''.replace('CHANNEL', channel).replace('METHODS', methods)
(WORK / 'NotificationPermissions.java').write_text(java)
jdk = Path(os.environ['JAVA_HOME']) if os.environ.get('JAVA_HOME') else next((ROOT/'.tmp/mobile-tools/jdk').glob('*/Contents/Home'))
subprocess.run([str(jdk/'bin/javac'), '-encoding', 'UTF-8', '-d', str(WORK), str(WORK/'NotificationPermissions.java')], check=True)
subprocess.run([str(jdk/'bin/java'), '-cp', str(WORK), 'NotificationPermissions'], check=True)

ios = (ROOT / 'mobile/ios/BridgePreview/App.swift').read_text()
assert 'requestStartupNotifications()' in section(ios, '    override func viewDidAppear(', '    override func viewDidLayoutSubviews()')
assert 'completion([.banner, .sound])' in ios and 'content.sound = .default' in ios
methods = section(ios, '    private func requestStartupNotifications()', '    private func currentTask(').replace('private func', 'func')
swift = r'''
import Foundation
final class MemoryDefaults {var values:[String:Bool]=[:];func object(forKey k:String)->Any? {values[k]};func bool(forKey k:String)->Bool {values[k] ?? false};func set(_ value:Bool,forKey k:String){values[k]=value}}
enum MobileStrings {static func text(_ value:String)->String {value}}
enum Option:Hashable {case alert,sound,badge}
final class UNUserNotificationCenter {
 static let instance=UNUserNotificationCenter();static func current()->UNUserNotificationCenter {instance}
 var requests=0;var options:[Option]=[];var callback:((Bool,Error?)->Void)?
 func requestAuthorization(options:[Option],completionHandler:@escaping (Bool,Error?)->Void){requests+=1;self.options=options;callback=completionHandler}
 func complete(_ allowed:Bool){let saved=callback;callback=nil;saved?(allowed,nil);RunLoop.current.run(until:Date().addingTimeInterval(0.02))}
}
final class BridgeController {
 let defaults=MemoryDefaults();var foregroundBaselines=Set<String>();var synced=0;var messages:[String]=[]
 func sync(){synced+=1};func info(_ value:String){messages.append(value)}
 METHODS
}
func check(_ value:Bool,_ message:String){precondition(value,message)}
let center=UNUserNotificationCenter.current(),fresh=BridgeController()
fresh.requestStartupNotifications();check(center.requests==1 && Set(center.options)==Set([.alert,.sound,.badge]),"first launch requests alert, sound and badge")
fresh.requestStartupNotifications();check(center.requests==1,"no duplicate pending request")
center.complete(true);check(fresh.defaults.bool(forKey:"foregroundAlerts") && fresh.synced==1,"grant enables alerts")
fresh.toggleForegroundAlerts();fresh.requestStartupNotifications();check(!fresh.defaults.bool(forKey:"foregroundAlerts") && center.requests==1,"explicit off survives relaunch")
let denied=BridgeController();denied.requestStartupNotifications();center.complete(false);let before=center.requests;denied.requestStartupNotifications();check(center.requests==before && denied.messages.isEmpty && !denied.defaults.bool(forKey:"foregroundAlerts"),"refusal stays quiet and does not repeat")
for enabled in [false,true] {let saved=BridgeController();saved.defaults.set(enabled,forKey:"foregroundAlerts");saved.requestStartupNotifications();check(center.requests==before && saved.defaults.bool(forKey:"foregroundAlerts")==enabled,"preserve existing user choice")}
denied.toggleForegroundAlerts();check(center.requests==before+1,"manual retry still works");center.complete(false);check(denied.messages.count==1,"manual denial gives settings guidance")
print("PASS iOS startup options, grant/refusal, no repeated prompt, stored choices and manual retry")
'''.replace('METHODS', methods)
(WORK/'main.swift').write_text(swift)
subprocess.run(['xcrun', 'swiftc', '-module-cache-path', str(WORK/'swift-cache'), str(WORK/'main.swift'), '-o', str(WORK/'ios-permissions')], check=True)
subprocess.run([str(WORK/'ios-permissions')], check=True)
