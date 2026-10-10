package io.github.try2love.codexbridge;
import android.content.Context;
import android.content.SharedPreferences;
import android.webkit.CookieManager;
import org.json.*;
import java.util.*;

/** Small foreground-only inbox snapshots. Never persist account data or open chats. */
final class ComputerNotifications {
 private static final class Snapshot {final String binding;final JSONArray clients;boolean fresh=true;Snapshot(String binding,JSONObject data){this.binding=binding;clients=data.optJSONArray("clients");}}
 private static final Map<String,Snapshot> snapshots=new HashMap<>();
 static volatile Runnable onChange;
 static synchronized void update(String origin,String binding,JSONObject data){snapshots.put(origin,new Snapshot(binding,data));changed();}
 static synchronized void forget(String origin){Snapshot snapshot=snapshots.get(origin);if(snapshot!=null)snapshot.fresh=false;changed();}
 static synchronized void stale(){for(Snapshot snapshot:snapshots.values())snapshot.fresh=false;changed();}
 static synchronized void clear(){snapshots.clear();changed();}
 private static void changed(){Runnable callback=onChange;if(callback!=null)callback.run();}
 static synchronized JSONArray clients(Context context,String origin){
  Snapshot snapshot=snapshots.get(origin);SharedPreferences prefs=context.getSharedPreferences("bridge",0);
  if(snapshot==null||!prefs.getStringSet("origins",Collections.emptySet()).contains(origin)||!snapshot.binding.equals(EventClient.sessionCookie(CookieManager.getInstance().getCookie(origin+"/"))))return new JSONArray();
  return snapshot.clients==null?new JSONArray():snapshot.clients;
 }
 static synchronized boolean fresh(String origin){Snapshot snapshot=snapshots.get(origin);return snapshot!=null&&snapshot.fresh;}
}
