package io.github.try2love.codexbridge;
import android.content.*;import android.webkit.CookieManager;import org.json.*;import java.util.*;

/** The runner injects the exact production moveChannel and Peer declarations. */
public class ChannelMoveTests extends Context {
 static final String NAME="codex_mobile_session_"+"1".repeat(24),ID="a".repeat(32),ROOT="r".repeat(64),NEW="n".repeat(64);
 static final String SOURCE="https://10.248.248.248:8788",SAME="https://10.248.248.248:8790",HTTP="http://10.248.248.248:8790",OTHER="https://other-fixture.invalid";
 final Prefs prefs=new Prefs();final WebView web=new WebView();final Handler homeHandler=new Handler();
 int channelTicket=1;String origin,pageOrigin,arrivingVia="";boolean pageReady=true;
 final Map<String,String> channelCookieOwners=new HashMap<>();
 Peer pendingPeer;final List<String> connected=new ArrayList<>();
 static class Prefs implements SharedPreferences,SharedPreferences.Editor {
  final Map<String,Object> data=new HashMap<>();public String getString(String k,String d){return data.get(k) instanceof String?(String)data.get(k):d;}
  @SuppressWarnings("unchecked") public Set<String> getStringSet(String k,Set<String>d){return data.get(k) instanceof Set?new HashSet<>((Set<String>)data.get(k)):d;}
  public Map<String,?> getAll(){return data;}public Editor edit(){return this;}public Editor putString(String k,String v){data.put(k,v);return this;}
  public Editor putStringSet(String k,Set<String>v){data.put(k,new HashSet<>(v));return this;}public Editor putBoolean(String k,boolean v){data.put(k,v);return this;}public void apply(){}
 }
 static class Handler{void postDelayed(Runnable r,long timeout){}}
 static class WebView{final Settings settings=new Settings();Settings getSettings(){return settings;}void evaluateJavascript(String s,Object callback){}}
 static class Settings{void setSupportMultipleWindows(boolean v){}void setJavaScriptCanOpenWindowsAutomatically(boolean v){}}
 static class NotificationCheckpoint{String stream="";static NotificationCheckpoint restore(String a,String b){return new NotificationCheckpoint();}String encode(String token){return "fixture";}}
 static class MonitorService{static void bindingsChanged(){}}
 static String L(String value){return value;}
 void connect(String address){connected.add(address);}void changedChannel(String address){}
 void endPeerWindow(){}
 Set<String> saved(){return prefs.getStringSet("origins",Collections.emptySet());}
 List<String> addresses(String address){try{return GatewayChannels.endpoints(GatewayChannels.record(prefs,address));}catch(Exception e){throw new AssertionError(e);}}
 ChannelMoveTests(String from,String to,boolean foreign)throws Exception{
  origin=pageOrigin=from;CookieManager.reset();DeviceBinding.values.clear();EventClient.names.clear();EventClient.setName(from,NAME);
  prefs.putStringSet("origins",new HashSet<>(List.of(from))).putString("channel-anchor:"+ID,from);
  JSONObject d=new JSONObject().put("deviceId",ID).put("anchor",from).put("endpoints",new JSONArray().put(from).put(to).put(OTHER));prefs.putString("channels:"+from,d.toString());
  if(foreign)prefs.putString("channels:"+to,new JSONObject().put("deviceId","b".repeat(32)).toString());
  DeviceBinding.save(this,GatewayChannels.vault(ID),NAME,ROOT);DeviceBinding.save(this,from,NAME,ROOT);
  CookieManager.seed(from,NAME+"="+ROOT+"; Path=/; HttpOnly"+(from.startsWith("https://")?"; Secure":""));
 }
 void begin(String to,boolean opening){moveChannel(origin,GatewayChannels.record(prefs,origin),to,origin+"/#draft",opening,()->true,false);CookieManager.drain();}
 boolean finish(){Peer peer=pendingPeer;pendingPeer=null;return peer!=null&&peer.commit.getAsBoolean();}
 static void check(boolean ok,String message){if(!ok)throw new AssertionError(message);}
 static String cookie(String address){return BindingSelection.cookie(CookieManager.getInstance().getCookie(address+"/"),NAME);}
 static int cases;
 public static void main(String[] args)throws Exception{
  for(boolean opening:new boolean[]{true,false})for(String[] pair:new String[][]{{SOURCE,SAME},{SOURCE,HTTP},{HTTP,SOURCE},{SOURCE,OTHER}}){
   ChannelMoveTests f=new ChannelMoveTests(pair[0],pair[1],false);f.begin(pair[1],opening);
   check(opening?f.connected.equals(List.of(pair[1]+"/#draft")):f.finish(),"known gateway moves "+Arrays.toString(pair)+" opening="+opening+" connected="+f.connected+" writes="+CookieManager.writes+" targetBound="+(DeviceBinding.load(f,pair[1])!=null));
   check(DeviceBinding.load(f,pair[1]).token.equals(GatewayChannels.token(ROOT,pair[0],pair[1])),"exact target credential");cases++;
  }
  for(boolean opening:new boolean[]{true,false}){
   ChannelMoveTests foreign=new ChannelMoveTests(SOURCE,SAME,true);foreign.begin(SAME,opening);check(CookieManager.writes==0&&foreign.pendingPeer==null,"foreign gateway never overwritten");cases++;
   ChannelMoveTests unknown=new ChannelMoveTests(SOURCE,OTHER,false);CookieManager.seed(OTHER,NAME+"="+NEW+"; Path=/; Secure; HttpOnly");unknown.begin(OTHER,opening);check(CookieManager.writes==0&&unknown.pendingPeer==null,"unknown cookie never overwritten");cases++;
  }
  ChannelMoveTests hidden=new ChannelMoveTests(HTTP,"http://10.248.248.248:8788",false);CookieManager.seed(SOURCE,NAME+"="+NEW+"; Path=/; Secure; HttpOnly");hidden.begin("http://10.248.248.248:8788",true);check(CookieManager.writes==0,"hidden secure unknown rejected");cases++;
  ChannelMoveTests root=new ChannelMoveTests(SOURCE,SAME,false);CookieManager.afterSet=()->{CookieManager.seed(SOURCE,NAME+"="+NEW+"; Path=/; Secure; HttpOnly");check(cookie(SOURCE).equals(NAME+"="+NEW),"new cookie accepted before root");DeviceBinding.save(root,GatewayChannels.vault(ID),NAME,NEW);};root.begin(SAME,false);check(root.pendingPeer==null&&DeviceBinding.load(root,GatewayChannels.vault(ID)).token.equals(NEW)&&cookie(SOURCE).equals(NAME+"="+NEW)&&DeviceBinding.load(root,SAME)==null&&CookieManager.writes==1,"new login during write wins");cases++;
  ChannelMoveTests rollback=new ChannelMoveTests(SOURCE,SAME,false);rollback.begin(SAME,false);rollback.pendingPeer.rollback.run();check(cookie(SOURCE).equals(NAME+"="+ROOT),"failed peer restores source");cases++;
  ChannelMoveTests later=new ChannelMoveTests(SOURCE,SAME,false);later.begin(SAME,false);CookieManager.seed(SOURCE,NAME+"="+NEW+"; Path=/; Secure; HttpOnly");later.pendingPeer.rollback.run();check(cookie(SOURCE).equals(NAME+"="+NEW),"failure preserves later cookie");cases++;
  ChannelMoveTests stale=new ChannelMoveTests(SOURCE,SAME,false);stale.begin(SAME,false);stale.channelTicket++;check(!stale.finish()&&DeviceBinding.load(stale,SAME)==null&&cookie(SOURCE).equals(NAME+"="+DeviceBinding.load(stale,SOURCE).token)&&stale.channelCookieOwners.isEmpty(),"old ticket rejects commit and restores source");cases++;
  ChannelMoveTests anchor=new ChannelMoveTests(SOURCE,SAME,false);anchor.begin(SAME,false);anchor.prefs.putString("channel-anchor:"+ID,SAME);check(!anchor.finish()&&DeviceBinding.load(anchor,SAME)==null,"changed anchor cannot commit");cases++;
  ChannelMoveTests concurrent=new ChannelMoveTests(SOURCE,SAME,false);concurrent.moveChannel(SOURCE,GatewayChannels.record(concurrent.prefs,SOURCE),SAME,SOURCE+"/",false,()->true,false);concurrent.channelTicket++;
  concurrent.moveChannel(SOURCE,GatewayChannels.record(concurrent.prefs,SOURCE),OTHER,SOURCE+"/",false,()->true,false);CookieManager.drain();check(concurrent.finish()&&concurrent.origin.equals(OTHER)&&DeviceBinding.load(concurrent,SAME)==null,"older callback cannot overwrite newer switch");cases++;
  ChannelMoveTests ticket=new ChannelMoveTests(SOURCE,SAME,false);CookieManager.afterSet=()->ticket.channelTicket++;ticket.begin(SAME,false);check(cookie(SOURCE).equals(NAME+"="+ROOT)&&DeviceBinding.load(ticket,SAME)==null&&ticket.channelCookieOwners.isEmpty(),"ticket during callback restores source");cases++;
  ChannelMoveTests identical=new ChannelMoveTests(SOURCE,SAME,false);identical.begin(SAME,false);Peer old=identical.pendingPeer;identical.pendingPeer=null;identical.channelTicket++;
  CookieManager.afterSet=old.rollback;identical.begin(SAME,false);int count=CookieManager.writes;old.rollback.run();check(CookieManager.writes==count&&identical.finish()&&identical.channelCookieOwners.isEmpty(),"new same-token owner survives old rollback");cases++;
  ChannelMoveTests queued=new ChannelMoveTests(SOURCE,SAME,false);queued.begin(SAME,false);Peer previous=queued.pendingPeer;queued.pendingPeer=null;queued.channelTicket++;previous.rollback.run();
  check(queued.channelCookieOwners.isEmpty(),"rollback releases only its own claims after enqueue");queued.channelTicket++;queued.begin(SAME,false);check(queued.finish()&&queued.channelCookieOwners.isEmpty(),"new operation before rollback callback survives");cases++;
  System.out.println("PASS exact Android moveChannel: "+cases+" cases; JDK CookieManager, not Android WebView acceptance");
 }
 // PRODUCTION_MOVE_CHANNEL
 // PRODUCTION_PEER
}
