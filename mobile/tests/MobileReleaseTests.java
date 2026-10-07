import io.github.try2love.codexbridge.MobileRelease;
public class MobileReleaseTests {
 static void check(boolean value){if(!value)throw new AssertionError();}
 public static void main(String[] args){
  String v="2.0.0-preview.2",name=MobileRelease.asset(v),url=new MobileRelease(v,"").download;
  check(MobileRelease.eligible("2.0.0-preview.1","v"+v,false,true,name,url));
  check(!MobileRelease.eligible("1.4.0","v"+v,false,true,name,url));
  check(!MobileRelease.eligible("1.4.0","v"+v,false,false,name,url));
  check(!MobileRelease.eligible(v,"v"+v,false,true,name,url));
  check(!MobileRelease.eligible("2.0.0-preview.1","v"+v,true,true,name,url));
  check(!MobileRelease.eligible("2.0.0-preview.1","v"+v,false,true,"other.apk",url));
  check(!MobileRelease.eligible("2.0.0-preview.1","v"+v,false,true,name,"https://evil.example/update.apk"));
  check(!MobileRelease.eligible("2.0.0-preview.1","v2.0.0-preview.02",false,true,name,url));
  check(MobileRelease.compare("2.0.0-preview.10","2.0.0-preview.9")>0);
  check(MobileRelease.compare("2.0.0","2.0.0-preview.99")>0);
  check(MobileRelease.compare("2.1.0-preview.1","2.0.0")>0);
  System.out.println("Java: mobile release channel, version and asset checks passed");
 }
}
