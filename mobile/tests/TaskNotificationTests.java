package io.github.try2love.codexbridge;

import android.content.Context;
import android.app.Notification;
import android.service.notification.StatusBarNotification;
import org.json.*;

public final class TaskNotificationTests {
 static void check(boolean condition,String message){if(!condition)throw new AssertionError(message);}
 static JSONObject event(String id,int sequence,String kind)throws Exception{
  return new JSONObject().put("id",id).put("sequence",sequence).put("threadId","same-chat")
      .put("host","local").put("kind",kind).put("title",kind).put("body",id);
 }
 static void show(Context c,String origin,String stream,String id,int sequence,String kind)throws Exception{
  TaskNotifications.show(c,origin,stream,event(id,sequence,kind),"foreground-tasks");
 }
 public static void main(String[] args)throws Exception{
  Context c=new Context();c.gateway=new JSONObject().put("deviceId","gateway-a");
  String lan="http://10.0.0.1:8788",https="https://gateway.example";
  show(c,lan,"stream-a","decision-a",1,"request");
  StatusBarNotification decision=c.manager.getActiveNotifications()[0];
  show(c,lan,"stream-a","completion-b",2,"completion");
  show(c,lan,"stream-a","decision-c",3,"request");
  check(c.manager.getActiveNotifications().length==3,"same-chat request/completion/later-request remain independent");
  StatusBarNotification[] notes=c.manager.getActiveNotifications();
  check(!notes[0].getNotification().tap.intent.data.toString().equals(notes[1].getNotification().tap.intent.data.toString()),"different event taps stay distinct");
  check("same-chat".equals(notes[0].getNotification().tap.intent.data.params.get("thread")),"original chat routing retained");
  check("local".equals(notes[0].getNotification().tap.intent.data.params.get("host")),"original host routing retained");
  check(!notes[1].getNotification().onlyAlertOnce,"new event may alert");
  show(c,https,"stream-a","decision-a",1,"request");
  check(c.manager.getActiveNotifications().length==3,"same event via another authenticated route updates one card");
  Notification repeated=c.manager.getActiveNotifications()[0].getNotification();
  check(repeated.onlyAlertOnce,"same event retry does not alert again");
  check(https.equals(repeated.tap.intent.data.params.get("origin")),"retry preserves a usable current route");
  show(c,https,"stream-a","push:stream-a:1",1,"request");
  check(c.manager.getActiveNotifications().length==3,"optional push and authenticated inbox share the stream sequence identity");
  TaskNotifications.clearChat(c,https,"same-chat","local");
  check(c.manager.getActiveNotifications().length==3,"viewing or entering a chat does not remove independent events");
  check(repeated.autoCancel,"tapping one event auto-dismisses that event");
  c.manager.cancel(decision.getTag(),decision.getId());
  check(c.manager.getActiveNotifications().length==2,"one dismissal leaves unrelated event cards");
  show(c,https,"stream-b","completion-b",2,"completion");
  check(c.manager.getActiveNotifications().length==3,"new stream keeps its own event namespace");
  c.gateway=new JSONObject().put("deviceId","gateway-b");
  show(c,https,"stream-a","completion-b",2,"completion");
  check(c.manager.getActiveNotifications().length==4,"other gateway keeps its own event namespace");
  c.enabled=false;show(c,https,"stream-a","muted",4,"request");
  check(c.manager.getActiveNotifications().length==4,"disabled source produces no notification");
  String legacy="chat|"+new JSONArray().put("gateway-b").put("local").put("same-chat");
  c.manager.notify(legacy,legacy.hashCode()&0x7fffffff,new Notification());
  TaskNotifications.clearChat(c,https,"same-chat","local");
  check(c.manager.getActiveNotifications().length==4,"legacy per-chat cleanup does not remove new events");
  Context providers=new Context();
  int sequence=0;
  for(String provider:new String[]{"codex","claude","deepseek","zcode"}){
   JSONObject value=event(provider,++sequence,"completion").put("provider",provider).put("host","desktop:"+provider);
   TaskNotifications.show(providers,https,"shared",value,"tasks");
  }
  check(providers.manager.getActiveNotifications().length==4,"all agent providers use independent event notifications");
  System.out.println("PASS independent events, exact-event retries, route identity, tap routing, dismissal and source isolation");
 }
}
