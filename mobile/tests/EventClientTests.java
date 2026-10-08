package io.github.try2love.codexbridge;

import android.webkit.CookieManager;
import com.sun.net.httpserver.HttpServer;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.atomic.AtomicReference;

public final class EventClientTests {
 public static void main(String[] args)throws Exception {
  HttpServer server=HttpServer.create(new InetSocketAddress("127.0.0.1",0),0);
  String origin="http://127.0.0.1:"+server.getAddress().getPort();
  AtomicReference<String> agent=new AtomicReference<>(""),mode=new AtomicReference<>("");
  CookieManager cookies=CookieManager.getInstance();
  server.createContext("/api/",exchange->{
   agent.set(exchange.getRequestHeaders().getFirst("User-Agent"));
   int status=mode.get().equals("denied")?401:200;
   if(mode.get().equals("replaced"))cookies.value="codex_mobile_session=newer-binding";
   if(mode.get().equals("removed"))cookies.value="";
   String attributes=mode.get().equals("domain")?"; Domain=127.0.0.1; Path=/":mode.get().equals("path")?"; Path=/other":"; Path=/";
   exchange.getResponseHeaders().set("Set-Cookie","codex_mobile_session=legacy-binding"+attributes+"; HttpOnly; SameSite=Strict; Max-Age=34560000");
   byte[] body="{}".getBytes(StandardCharsets.UTF_8);exchange.sendResponseHeaders(status,body.length);exchange.getResponseBody().write(body);exchange.close();
  });server.start();
  try{
   for(boolean notifications:new boolean[]{true,false}){
    cookies.reset();mode.set("");
    if(notifications)EventClient.read(origin);else EventClient.request(origin,"/api/auth",null,null);
    check(agent.get().contains("BridgeMobile/0.1-Android"),"Native background requests identify the App so a valid legacy session upgrades");
    check(cookies.lastSet.contains("Max-Age=34560000"),"Renewal reaches the WebView cookie store");
    check(cookies.flushes==1,"Renewal is persisted before returning");
   }
   for(String state:new String[]{"denied","replaced","removed","domain","path"}){
    cookies.reset();mode.set(state);
    try{EventClient.read(origin);if(state.equals("denied"))throw new AssertionError("Unauthorized request accepted");}catch(java.io.IOException expected){if(!state.equals("denied"))throw expected;}
    check(cookies.lastSet.isEmpty(),"Late/denied renewal must not restore or overwrite a binding: "+state);
   }
   // Pause renewal after its cookie read and race a real local removal.
   // With synchronization removal waits; without it, renewal restores the
   // captured old value after removal and this final assertion fails.
   cookies.reset();mode.set("");AtomicReference<Thread> removing=new AtomicReference<>();
   cookies.afterRead=()->{
    Thread thread=new Thread(()->EventClient.forget(origin));removing.set(thread);thread.start();
    long deadline=System.nanoTime()+2_000_000_000L;
    while(thread.isAlive()&&thread.getState()!=Thread.State.BLOCKED&&System.nanoTime()<deadline)Thread.yield();
    check(!thread.isAlive()||thread.getState()==Thread.State.BLOCKED,"Removal reached its cookie operation");
   };
   EventClient.read(origin);removing.get().join(2000);
   check(!removing.get().isAlive()&&!cookies.value.contains("legacy-binding"),"Concurrent local removal wins over an in-flight renewal");
   System.out.println("Native Android background renewal checks passed");
  }finally{server.stop(0);}
 }
 static void check(boolean value,String message){if(!value)throw new AssertionError(message);}
}
