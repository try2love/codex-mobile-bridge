#!/usr/bin/env python3
"""Exercise the Android production reachability probe with isolated HTTP fixtures."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / '.tmp/android-reachability-tests'
WORK.mkdir(parents=True, exist_ok=True)
jar = ROOT / '.tmp/mobile-tools/json-20240303.jar'
jar.parent.mkdir(parents=True, exist_ok=True)
if not jar.exists():
    urllib.request.urlretrieve('https://repo.maven.apache.org/maven2/org/json/json/20240303/json-20240303.jar', jar)
assert hashlib.sha256(jar.read_bytes()).hexdigest() == '3cf6cd6892e32e2b4c1c39e0f52f5248a2f5b37646fdfbb79a66b46b618414ed'
clock = WORK / 'android/os/SystemClock.java'
clock.parent.mkdir(parents=True, exist_ok=True)
clock.write_text('package android.os;public class SystemClock {public static long elapsedRealtime(){return System.nanoTime()/1000000;}}')
runner = WORK / 'GatewayProbeTests.java'
runner.write_text(r'''
package io.github.try2love.codexbridge;
import com.sun.net.httpserver.*;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;

public class GatewayProbeTests {
 static final class Fixture implements AutoCloseable {
  final HttpServer server=HttpServer.create(new InetSocketAddress("127.0.0.1",0),0);
  final AtomicInteger requests=new AtomicInteger();final AtomicBoolean credentials=new AtomicBoolean();
  final CountDownLatch second=new CountDownLatch(1);final ExecutorService workers=Executors.newCachedThreadPool(r->{Thread t=new Thread(r);t.setDaemon(true);return t;});
  Fixture(String kind)throws Exception {
   server.setExecutor(workers);server.createContext("/",exchange->{
    if(requests.incrementAndGet()>1)second.countDown();
    if(exchange.getRequestHeaders().getFirst("Cookie")!=null||exchange.getRequestHeaders().getFirst("Authorization")!=null)credentials.set(true);
    try{
     if(kind.equals("slow"))Thread.sleep(4300);
     if(kind.equals("timeout"))Thread.sleep(14000);
     String text="{\"authenticated\":false,\"passwordless\":false,\"instanceId\":\"fixture\"}";
     if(kind.equals("html"))text="<html>not a gateway</html>";
     if(kind.equals("wrong"))text="{\"status\":\"ok\"}";
     if(kind.equals("large"))text="x".repeat(17000);
     if(kind.equals("offline"))text="{\"authenticated\":false,\"passwordless\":false,\"instanceId\":\"fixture\",\"relay\":true,\"online\":false}";
     int status=kind.equals("error")?503:kind.equals("redirect")?302:200;
     if(status==302)exchange.getResponseHeaders().add("Location",origin()+"/redirected");
     byte[] body=text.getBytes(StandardCharsets.UTF_8);exchange.sendResponseHeaders(status,body.length);
     if(kind.equals("drip")){for(byte value:body){exchange.getResponseBody().write(value);exchange.getResponseBody().flush();Thread.sleep(900);}}
     else exchange.getResponseBody().write(body);
    }catch(InterruptedException interrupted){Thread.currentThread().interrupt();}catch(IOException cancelled){}finally{exchange.close();}
   });server.start();
  }
  String origin(){return "http://127.0.0.1:"+server.getAddress().getPort();}
  public void close(){server.stop(0);workers.shutdownNow();}
 }
 static void check(String kind,boolean expected,int count)throws Exception {
  try(Fixture fixture=new Fixture(kind)){
   long start=System.nanoTime();boolean result=new GatewayProbe().check(fixture.origin());
   if(result!=expected)throw new AssertionError(kind+": "+result);
   if(fixture.requests.get()!=count)throw new AssertionError(kind+": request count "+fixture.requests);
   if(fixture.credentials.get())throw new AssertionError("probe sent credentials");
   if(TimeUnit.NANOSECONDS.toMillis(System.nanoTime()-start)>13000)throw new AssertionError("probe exceeded total budget");
   System.out.println(kind+" passed");
  }
 }
 public static void main(String[] args)throws Exception {
  for(String kind:new String[]{"valid","html","wrong","large","error","redirect","offline"})check(kind,kind.equals("valid"),1);
  check("slow",true,2);check("timeout",false,2);check("drip",false,2);
  try(Fixture fixture=new Fixture("timeout")){
   GatewayProbe probe=new GatewayProbe();ExecutorService worker=Executors.newSingleThreadExecutor();
   try{
    Future<Boolean> pending=worker.submit(()->probe.check(fixture.origin()));
    if(!fixture.second.await(6,TimeUnit.SECONDS))throw new AssertionError("slow confirmation did not start");
    probe.cancel();if(pending.get(2,TimeUnit.SECONDS))throw new AssertionError("cancelled probe succeeded");
    if(fixture.requests.get()!=2)throw new AssertionError("cancel caused extra retry");
   }finally{worker.shutdownNow();}
  }
  System.out.println("slow-confirmation cancellation passed");
 }
}
''')
java_home = os.environ.get('JAVA_HOME')
def java_tool(name):
    return str(Path(java_home) / 'bin' / name) if java_home else shutil.which(name)
source = ROOT / 'mobile/android/src/io/github/try2love/codexbridge'
subprocess.run([java_tool('javac'), '-encoding', 'UTF-8', '-cp', str(jar), '-d', str(WORK),
                str(clock), str(source/'GatewayURL.java'), str(source/'GatewayProbe.java'), str(runner)], check=True)
subprocess.run([java_tool('java'), '-cp', str(WORK)+os.pathsep+str(jar),
                'io.github.try2love.codexbridge.GatewayProbeTests'], check=True)
