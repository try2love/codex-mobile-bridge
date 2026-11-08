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
  byte[] diagnostics="{\"hosts\":[],\"phase\":\"discovery\"}".getBytes("UTF-8");
  server.createContext("/api/sessions/diagnostics",e->{
   if(!"fixture=session".equals(e.getRequestHeaders().getFirst("Cookie"))){e.sendResponseHeaders(401,-1);e.close();return;}
   e.getResponseHeaders().set("Content-Disposition","attachment; filename=codex-session-diagnostics.json");
   e.sendResponseHeaders(200,diagnostics.length);e.getResponseBody().write(diagnostics);e.close();
  });
  server.start();
  try{
   ArtifactDownload.Result r=ArtifactDownload.fetch(base+path+"?host=local",base,"fixture=session",directory.toFile());
   check(Arrays.equals(Files.readAllBytes(r.file.toPath()),payload),"download exact bytes");
   check(r.name.equals("Bridge Preview.apk"),"UTF-8 suggested filename");r.file.delete();
   check(!ArtifactDownload.accepts("https://evil.example"+path,base),"external origin rejected");
   check(!ArtifactDownload.accepts(base+"/api/auth",base),"non-artifact route rejected");
   String report=base+"/api/sessions/diagnostics";
   String thread="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee";
   for(String imagePath:new String[]{"/api/sessions/"+thread+"/uploads/"+thread+"/preview","/api/sessions/"+thread+"/desktop-images/"+String.join("",Collections.nCopies(64,"a")),"/api/desktop-sessions/zcode/uploads/"+thread+"/preview","/api/desktop-sessions/claude/images/"+String.join("",Collections.nCopies(64,"a"))}){
    check(ArtifactDownload.accepts(base+imagePath+"?download=1&sessionId=fixture",base),"image save accepted");
    for(String suffix:new String[]{"","?download=0","?download=1&download=0","?download=1#fragment"})check(!ArtifactDownload.accepts(base+imagePath+suffix,base),"preview stays out of download navigation");
    check(!ArtifactDownload.accepts("https://other.invalid"+imagePath+"?download=1",base),"image origin is retained");
   }
   check(ArtifactDownload.accepts(report,base),"diagnostic route accepted");
   check(ArtifactDownload.accepts(report+"?thread="+thread,base),"targeted diagnostic accepted");
   for(String suffix:new String[]{"?thread=bad","?thread="+thread+"&extra=1","?other=1","/extra","#fragment"})
    check(!ArtifactDownload.accepts(report+suffix,base),"invalid diagnostic route rejected: "+suffix);
   String scope=base+"/d/"+String.join("",Collections.nCopies(32,"a"));
   check(ArtifactDownload.accepts(scope+"/api/sessions/diagnostics",scope),"scoped diagnostic accepted");
   check(!ArtifactDownload.accepts(scope+"/api/sessions/diagnostics",base+"/d/"+String.join("",Collections.nCopies(32,"b"))),"other relay scope rejected");
   ArtifactDownload.Result reportResult=ArtifactDownload.fetch(report,base,"fixture=session",directory.toFile());
   check(Arrays.equals(Files.readAllBytes(reportResult.file.toPath()),diagnostics),"diagnostic 200 bytes saved");
   check(reportResult.name.equals("codex-session-diagnostics.json"),"diagnostic filename saved");reportResult.file.delete();
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
