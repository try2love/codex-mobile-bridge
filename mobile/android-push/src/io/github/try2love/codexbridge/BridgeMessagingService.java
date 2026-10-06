package io.github.try2love.codexbridge;
import com.google.firebase.messaging.FirebaseMessagingService;
import com.google.firebase.messaging.RemoteMessage;
public final class BridgeMessagingService extends FirebaseMessagingService {
 @Override public void onNewToken(String token){getSharedPreferences("bridge",0).edit().putString("fcmToken",token).apply();try{EventClient.register(getApplicationContext(),token);}catch(Exception ignored){/* Next foreground entry retries against the authenticated gateway. */}}
 @Override public void onMessageReceived(RemoteMessage message){/* Foreground uses the authenticated inbox; background notification payloads are displayed by FCM. */}
}
