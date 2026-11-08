package io.github.try2love.codexbridge;

import android.app.*;
import android.content.*;
import android.net.Uri;
import android.os.Bundle;
import java.util.Arrays;
import org.json.JSONObject;

/** System presentation shared by the authenticated inbox and optional FCM host. */
final class TaskNotifications {
 static void show(Context context, String origin, JSONObject event) throws Exception {
  show(context, origin, "test", event, "foreground-tasks");
 }

 static void show(Context context, String origin, String stream, JSONObject event, String channel) throws Exception {
  synchronized (EventClient.class) {
  if (!NotificationSources.enabled(context.getSharedPreferences("bridge", 0), origin)) return;
  NotificationManager manager = (NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE);
  if (!manager.areNotificationsEnabled()) throw new java.io.IOException("Notifications disabled");
  createChannel(context, channel);
  String thread = event.optString("threadId"), host = event.optString("host", "local");
  String gateway = gatewayIdentity(context, origin);
  // Sequence is unique within a stream and also survives optional FCM delivery.
  String eventId = event.optString("sequence", event.getString("id"));
  String eventKey = new org.json.JSONArray().put(stream).put(eventId).toString();
  String tag = "event|" + new org.json.JSONArray().put(gateway).put(stream).put(eventId);
  boolean repeated = false;
  // A new event may alert again; retrying the same event only updates its existing card.
  for (android.service.notification.StatusBarNotification note : manager.getActiveNotifications()) {
   if (tag.equals(note.getTag())) {
    repeated = eventKey.equals(note.getNotification().extras.getString("bridgeEvent"));
    break;
   }
  }
  int id = tag.hashCode() & 0x7fffffff;
  // Intent data preserves distinct taps even if two notification IDs have the same hash.
  Uri link = new Uri.Builder().scheme("codexbridge").authority("open")
      .appendQueryParameter("origin", origin).appendQueryParameter("thread", thread)
      .appendQueryParameter("host", host).appendQueryParameter("event", tag).build();
  Intent intent = new Intent(context, MainActivity.class).setData(link)
      .setFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP);
  PendingIntent tap = PendingIntent.getActivity(context, id, intent,
      PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
  Bundle metadata = new Bundle();
  metadata.putString("bridgeOrigin", origin);
  metadata.putLong("bridgeSequence", event.optLong("sequence"));
  metadata.putString("bridgeEvent", eventKey);
  String provider = event.optString("provider");
  if (!Arrays.asList("codex", "claude", "deepseek", "zcode").contains(provider)) {
   provider = host.matches("desktop:(claude|deepseek)(:[0-9a-f]{32})?") ? host.split(":")[1] : host.equals("desktop:zcode") ? "zcode" : "codex";
  }
  String source = provider.equals("claude") ? "Claude" : provider.equals("deepseek") ? "DeepSeek Harness" : provider.equals("zcode") ? "ZCode" : "Codex";
  Notification.Builder notification = new Notification.Builder(context, channel)
      .setSmallIcon(context.getResources().getIdentifier("ic_notification", "drawable", context.getPackageName()))
      .addExtras(metadata).setContentTitle(event.getString("title")).setContentText(event.getString("body"))
      .setSubText(source).setOnlyAlertOnce(repeated).setVisibility(Notification.VISIBILITY_PRIVATE)
      .setContentIntent(tap).setAutoCancel(true);
  try (java.io.InputStream image = context.getAssets().open(provider + ".png")) {
   notification.setLargeIcon(android.graphics.BitmapFactory.decodeStream(image));
  } catch (java.io.IOException ignored) { /* Artwork availability never gates delivery. */ }
  manager.notify(tag, id, notification.build());
  }
 }

 private static String gatewayIdentity(Context context, String origin) {
  JSONObject channel = GatewayChannels.record(context.getSharedPreferences("bridge",0), origin);
  return channel == null ? origin : channel.optString("deviceId", origin);
 }

 private static String chatTag(String origin, String host, String thread) {
  return "chat|" + new org.json.JSONArray().put(origin).put(host).put(thread);
 }

 static void clearChat(Context context, String origin, String thread, String host) {
  // Only migrate the old per-chat card. New event cards survive viewing the chat;
  // each is dismissed by its own tap/swipe or the explicit inbox-clear action.
  String tag = chatTag(gatewayIdentity(context, origin), host, thread);
  ((NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE)).cancel(tag, tag.hashCode() & 0x7fffffff);
 }

 static void createChannel(Context context, String id) {
  NotificationManager manager = (NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE);
  // Existing channels belong to the user; preserve their mute and sound settings.
  if (manager.getNotificationChannel(id) != null) return;
  NotificationChannel channel = new NotificationChannel(id, MobileStrings.text(context, "任务提醒"),
      NotificationManager.IMPORTANCE_HIGH);
  channel.setSound(android.provider.Settings.System.DEFAULT_NOTIFICATION_URI,
      new android.media.AudioAttributes.Builder().setUsage(android.media.AudioAttributes.USAGE_NOTIFICATION)
          .setContentType(android.media.AudioAttributes.CONTENT_TYPE_SONIFICATION).build());
  manager.createNotificationChannel(channel);
 }
}
