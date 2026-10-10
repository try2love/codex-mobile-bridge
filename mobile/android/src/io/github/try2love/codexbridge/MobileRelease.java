package io.github.try2love.codexbridge;

import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Release selection is independent of the gateway and never changes its login. */
public final class MobileRelease {
 public static final String REPO="https://github.com/try2love/codex-mobile-bridge";
 public static final String API="https://api.github.com/repos/try2love/codex-mobile-bridge/releases?per_page=100";
 private static final Pattern VERSION=Pattern.compile("^(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)(?:-(alpha|beta|preview|rc)\\.(0|[1-9][0-9]*))?$");
 public final String version,notes,download;
 public MobileRelease(String version,String notes){this.version=version;this.notes=notes;download=REPO+"/releases/download/v"+version+"/"+asset(version);}
 public static String asset(String version){return "Codex-Mobile-Bridge-"+version+"-Android.apk";}
 public String page(){return REPO+"/releases/tag/v"+version;}
 private static long[] parts(String value){
  Matcher m=VERSION.matcher(value);if(!m.matches())throw new IllegalArgumentException("Invalid version");
  int stage=m.group(4)==null?4:java.util.Arrays.asList("alpha","beta","preview","rc").indexOf(m.group(4));
  return new long[]{Long.parseLong(m.group(1)),Long.parseLong(m.group(2)),Long.parseLong(m.group(3)),stage,Long.parseLong(m.group(5)==null?"0":m.group(5))};
 }
 public static int compare(String a,String b){long[] x=parts(a),y=parts(b);for(int i=0;i<x.length;i++)if(x[i]!=y[i])return Long.compare(x[i],y[i]);return 0;}
 public static boolean eligible(String current,String tag,boolean draft,boolean prerelease,String name,String url){
  try{
   if(draft||!tag.startsWith("v"))return false;String v=tag.substring(1);
   if(!current.contains("-")&&(prerelease||v.contains("-")))return false;
   return compare(v,current)>0&&name.equals(asset(v))&&url.equals(new MobileRelease(v,"").download);
  }catch(RuntimeException e){return false;}
 }
}
