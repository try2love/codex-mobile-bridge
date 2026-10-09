package io.github.try2love.codexbridge;

import android.content.SharedPreferences;
import android.webkit.CookieManager;
import org.json.*;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;

/** Production transport + service loop; only OS storage/delivery are substituted. */
public final class MobileEventTests {
 static final List<String> delivered=new CopyOnWriteArrayList<>();
 static final class Prefs implements SharedPreferences,SharedPreferences.Editor {
  final Map<String,Object> values=new ConcurrentHashMap<>();
  public boolean getBoolean(String k,boolean d){return (boolean)values.getOrDefault(k,d);}public String getString(String k,String d){return (String)values.getOrDefault(k,d);}public long getLong(String k,long d){return ((Number)values.getOrDefault(k,d)).longValue();}
  @SuppressWarnings("unchecked") public Set<String> getStringSet(String k,Set<String> d){return (Set<String>)values.getOrDefault(k,d);}
  public Editor edit(){return this;}public Editor putString(String k,String v){values.put(k,v);return this;}public Editor putBoolean(String k,boolean v){values.put(k,v);return this;}public Editor putLong(String k,long v){values.put(k,v);return this;}public void apply(){}
 }
 static final class Gateway implements AutoCloseable {
  final ServerSocket socket=new ServerSocket(0,16,InetAddress.getByName("127.0.0.1"));
  final ExecutorService workers=Executors.newCachedThreadPool();
  final List<String> targets=new CopyOnWriteArrayList<>();
  final List<Integer> requestBytes=new CopyOnWriteArrayList<>(),responseBytes=new CopyOnWriteArrayList<>();
  volatile String stream="fixture-stream";volatile int cursor=200,status=200;volatile boolean enabled=true;
  volatile CountDownLatch received,release;final AtomicBoolean slow=new AtomicBoolean();
  Gateway()throws Exception {workers.submit(()->{while(!socket.isClosed())try{Socket client=socket.accept();workers.submit(()->serve(client));}catch(IOException ignored){}});}
  String origin(){return "http://127.0.0.1:"+socket.getLocalPort();}
  void hold(){received=new CountDownLatch(1);release=new CountDownLatch(1);slow.set(true);}
  void serve(Socket client){try(client){
   ByteArrayOutputStream raw=new ByteArrayOutputStream();int c;while((c=client.getInputStream().read())!=-1){raw.write(c);byte[] b=raw.toByteArray();int n=b.length;if(n>=4&&b[n-4]==13&&b[n-3]==10&&b[n-2]==13&&b[n-1]==10)break;}
   String headers=raw.toString("UTF-8"),target=headers.split(" ")[1];targets.add(target);requestBytes.add(raw.size());
   check(headers.toLowerCase(Locale.ROOT).contains("origin: "+origin()),"Origin header retained");check(headers.contains(EventClient.USER_AGENT),"Native user agent retained");
   if(slow.compareAndSet(true,false)){received.countDown();release.await(5,TimeUnit.SECONDS);}
   long after=target.contains("?after=")?Long.parseLong(target.split("\\?after=")[1]):0;
   JSONArray events=new JSONArray();for(int i=Math.max(1,cursor-199);i<=cursor;i++)if(i>after)events.put(new JSONObject().put("id","event-"+i).put("sequence",i).put("threadId","thread").put("host","local").put("kind",i%2==0?"request":"completion").put("title","任务提醒").put("body","消息内容".repeat(30)));
   byte[] body=new JSONObject().put("streamId",stream).put("cursor",cursor).put("events",events).put("enabled",enabled).toString().getBytes(StandardCharsets.UTF_8);
   String head="HTTP/1.1 "+status+" OK\r\nContent-Type: application/json\r\nContent-Length: "+body.length+"\r\nConnection: close\r\n\r\n";
   client.getOutputStream().write(head.getBytes(StandardCharsets.UTF_8));client.getOutputStream().write(body);responseBytes.add(body.length);
  }catch(Exception ignored){}}
  public void close()throws Exception {socket.close();if(release!=null)release.countDown();workers.shutdownNow();}
 }
 static void check(boolean v,String message){if(!v)throw new AssertionError(message);}
 static void poll(MonitorService service)throws Exception {var method=MonitorService.class.getDeclaredMethod("check");method.setAccessible(true);method.invoke(service);}
 static void visible(boolean value)throws Exception {
  try{var method=MonitorService.class.getDeclaredMethod("setVisible",boolean.class);method.setAccessible(true);method.invoke(null,value);}
  catch(NoSuchMethodException baseline){MonitorService.visible=value;if(value)MonitorService.foregroundEpoch++;}
 }
 static void metric(String label,Gateway gateway,int index){System.out.println(label+" request_bytes="+gateway.requestBytes.get(index)+" response_body_bytes="+gateway.responseBytes.get(index)+" target="+gateway.targets.get(index));}
 public static void main(String[] args)throws Exception {
  boolean baseline=args.length>0&&args[0].equals("baseline");Prefs prefs=new Prefs();MonitorService service=new MonitorService();service.preferences=prefs;prefs.putBoolean("alerts",true);
  try(Gateway first=new Gateway();Gateway second=new Gateway()){
   String address=first.origin(),key="cursor:"+address;prefs.values.put("origins",Set.of(address));visible(true);
   poll(service);check(delivered.isEmpty(),"First foreground baseline never replays history");poll(service);
   metric("initial",first,0);metric("unchanged",first,1);
   if(baseline){check(first.responseBytes.get(0).equals(first.responseBytes.get(1)),"Baseline repeatedly downloads history");System.out.println("Android baseline captured");return;}
   check(first.targets.get(1).endsWith("?after=200"),"Incremental poll must use persisted cursor");check(first.responseBytes.get(1)<100,"No-change response contains metadata only");
   first.cursor=201;poll(service);check(delivered.equals(List.of(address+":201")),"New completion delivered exactly once");metric("one_new",first,2);poll(service);check(delivered.size()==1,"Repeated cursor never duplicates notification");
   prefs.putLong("cleared:"+address+":"+first.stream,202);first.cursor=202;poll(service);check(delivered.size()==1,"Cleared notification suppressed");first.cursor=203;poll(service);check(delivered.size()==2,"New request after clear delivered");
   first.stream="reset-lower";first.cursor=1;poll(service);check(prefs.getLong(key,-1)==1&&delivered.size()==2,"Lower reset cursor rebaselines");first.cursor=2;poll(service);check(delivered.size()==3,"Lower reset does not starve later events");
   first.stream="reset-higher";first.cursor=300;poll(service);check(prefs.getLong(key,-1)==300&&delivered.size()==3,"Higher stream reset rebaselines");first.cursor=301;poll(service);check(delivered.size()==4,"Higher reset delivers later event");
   first.status=401;first.cursor=302;poll(service);check(prefs.getLong(key,-1)==301,"Expired login cannot advance cursor");first.status=200;CookieManager.getInstance().value="codex_mobile_session=new-login";visible(true);poll(service);check(delivered.size()==4,"Re-login foreground suppresses old events");first.cursor=303;poll(service);check(delivered.size()==5,"Re-login delivers subsequent event");
   JSONObject inbox=EventClient.read(address);check(inbox.getJSONArray("events").length()==200&&!first.targets.get(first.targets.size()-1).contains("?"),"Inbox still reads all retained historical events");
   // Both origins block their first request; HashSet iteration order is irrelevant.
   prefs.values.put("origins",Set.of(address,second.origin()));first.hold();second.hold();int before=first.targets.size()+second.targets.size();
   AtomicReference<Throwable> failure=new AtomicReference<>();Thread batch=new Thread(()->{try{poll(service);}catch(Throwable e){failure.set(e);}});batch.start();
   long deadline=System.nanoTime()+2_000_000_000L;while(first.targets.size()+second.targets.size()==before&&System.nanoTime()<deadline)Thread.sleep(5);
   check(first.targets.size()+second.targets.size()==before+1,"First request reached gateway");visible(false);first.release.countDown();second.release.countDown();batch.join(2000);
   check(!batch.isAlive()&&failure.get()==null,"Background cancels pending poll");check(first.targets.size()+second.targets.size()==before+1,"Background issues zero requests to subsequent computers");check(prefs.getLong(key,-1)==303,"Background response cannot advance prior cursor");
   // Resume immediately while a cancelled response is still pending.
   prefs.values.put("origins",Set.of(address));first.hold();visible(true);Thread old=new Thread(()->{try{poll(service);}catch(Exception e){failure.set(e);}});old.start();check(first.received.await(2,TimeUnit.SECONDS),"Old request started");visible(false);visible(true);first.cursor=310;poll(service);first.release.countDown();old.join(2000);check(prefs.getLong(key,-1)==310&&delivered.size()==5,"Late cancelled batch cannot mutate resumed baseline");first.cursor=311;poll(service);check(delivered.size()==6,"Resume remains live after old callback");
   // Switching login while a response is pending cannot commit its old cursor.
   first.hold();first.cursor=312;Thread replaced=new Thread(()->{try{poll(service);}catch(Exception e){failure.set(e);}});replaced.start();check(first.received.await(2,TimeUnit.SECONDS),"Login race request started");CookieManager.getInstance().value="codex_mobile_session=replacement";first.release.countDown();replaced.join(2000);check(prefs.getLong(key,-1)==311&&delivered.size()==6,"Replaced login response cannot commit cursor");check(CookieManager.getInstance().value.endsWith("replacement"),"Replaced login cannot be overwritten");MonitorService.bindingsChanged();poll(service);check(prefs.getLong(key,-1)==312&&delivered.size()==6,"New login establishes baseline");
   CookieManager cookies=CookieManager.getInstance();cookies.reads=0;cookies.switchAt=4;cookies.afterRead=()->{cookies.value="codex_mobile_session=commit-window";};first.cursor=313;poll(service);cookies.afterRead=null;check(prefs.getLong(key,-1)==312&&delivered.size()==6,"Login change after HTTP read but before commit cannot advance cursor");MonitorService.bindingsChanged();poll(service);check(prefs.getLong(key,-1)==313&&delivered.size()==6,"Commit-window replacement establishes a fresh baseline");
   // Removing a connection while its response is delayed must not restore state.
   first.hold();Thread removed=new Thread(()->{try{poll(service);}catch(Exception e){failure.set(e);}});removed.start();check(first.received.await(2,TimeUnit.SECONDS),"Removal race request started");prefs.values.put("origins",Set.of());prefs.values.remove(key);first.release.countDown();removed.join(2000);check(!prefs.values.containsKey(key),"Removed connection cursor cannot resurrect");
   visible(false);
   MonitorService scheduledService=new MonitorService();scheduledService.preferences=prefs;scheduledService.onCreate();
   var schedule=MonitorService.class.getDeclaredField("scheduled");schedule.setAccessible(true);
   check(schedule.get(scheduledService)==null,"Background service owns no polling timer");visible(true);ScheduledFuture<?> foreground=(ScheduledFuture<?>)schedule.get(scheduledService);check(foreground!=null&&!foreground.isCancelled(),"Foreground starts polling timer");visible(false);check(foreground.isCancelled()&&schedule.get(scheduledService)==null,"Background cancels polling timer");visible(true);check(schedule.get(scheduledService)!=null,"Foreground restores timer");scheduledService.onDestroy();
   System.out.println("Android event sync: 17 scenarios passed; background subsequent-origin requests=0; background polling timer=none");
  }
 }
}
