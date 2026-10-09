import io.github.try2love.codexbridge.ArtifactDownload;
import com.sun.net.httpserver.HttpServer;
import java.net.*;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.atomic.AtomicInteger;

public class ArtifactDownloadTests {
 public static void main(String[] args)throws Exception {
  Path directory=Paths.get(args[0]);Files.createDirectories(directory);
  HttpServer server=HttpServer.create(new InetSocketAddress("127.0.0.1",0),0);
  String base="http://127.0.0.1:"+server.getAddress().getPort();
  String path="/api/sessions/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/files/"+String.join("",Collections.nCopies(64,"a"));
  byte[] payload=new byte[600123];new Random(7).nextBytes(payload);
  AtomicInteger leaked=new AtomicInteger();
  server.createContext("/redirect-target",e->{leaked.incrementAndGet();e.sendResponseHeaders(200,-1);e.close();});
  server.createContext(path,e->{
   String query=e.getRequestURI().getQuery();
   if("redirect".equals(query)){e.getResponseHeaders().set("Location",base+"/redirect-target");e.sendResponseHeaders(302,-1);e.close();return;}
   if("unauthorized".equals(query)||!"fixture=session".equals(e.getRequestHeaders().getFirst("Cookie"))){e.sendResponseHeaders(401,-1);e.close();return;}
   if("oversize".equals(query)){e.sendResponseHeaders(200,9007199254740992L);e.close();return;}
   e.getResponseHeaders().set("Content-Disposition","attachment; filename*=UTF-8''Bridge%20Preview.apk");
   e.sendResponseHeaders(200,payload.length);e.getResponseBody().write(payload);e.close();
  });
  server.start();
  try{
   ArtifactDownload.Result r=ArtifactDownload.fetch(base+path+"?host=local",base,"fixture=session",directory.toFile());
   check(Arrays.equals(Files.readAllBytes(r.file.toPath()),payload),"download exact bytes");
   check(r.name.equals("Bridge Preview.apk"),"UTF-8 suggested filename");r.file.delete();
   check(!ArtifactDownload.accepts("https://evil.example"+path,base),"external origin rejected");
   check(!ArtifactDownload.accepts(base+"/api/auth",base),"non-artifact route rejected");
   for(String query:new String[]{"redirect","unauthorized","oversize"}){
    try{ArtifactDownload.fetch(base+path+"?"+query,base,"fixture=session",directory.toFile());throw new AssertionError(query);}catch(java.io.IOException expected){}
   }
   check(ArtifactDownload.accepts(base+"/api/sessions/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/workspace/download?path=README.md",base),"workspace download accepted");
   check(!ArtifactDownload.accepts(base+"/api/sessions/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/workspace/upload",base),"workspace upload not downloaded");
   check(leaked.get()==0,"redirect never followed");
   check(ArtifactDownload.filename("attachment; filename*=UTF-8''%E6%96%87%E4%BB%B6%2B1.apk").equals("文件+1.apk"),"Unicode and plus preserved");
   check(ArtifactDownload.filename("attachment; filename=\"../../escape.apk\"").equals("escape.apk"),"path traversal removed");
   check(ArtifactDownload.filename("attachment; filename=\"..\"").equals("download"),"dot filename rejected");
   try(java.util.stream.Stream<Path> files=Files.list(directory)){check(files.count()==0,"failed transfers leave no files");}
   System.out.println("14 download checks passed (authenticated bytes, filenames, origin, redirect, errors, cleanup)");
  }finally{server.stop(0);}
 }
 static void check(boolean value,String label){if(!value)throw new AssertionError(label);}
}
