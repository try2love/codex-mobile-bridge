package io.github.try2love.codexbridge;

// Asset loading only; WebView navigation and prompt validation stay in MainActivity.
final class MobileWebScripts {
 static String route(android.content.res.AssetManager assets) throws java.io.IOException {
  try(java.io.InputStream in=assets.open("channel-link.js");java.io.ByteArrayOutputStream out=new java.io.ByteArrayOutputStream()){
   byte[] buf=new byte[4096];int n;while((n=in.read(buf))!=-1)out.write(buf,0,n);return out.toString("UTF-8");
  }
 }
 static String appearance(android.content.res.AssetManager assets){try(java.io.ByteArrayOutputStream out=new java.io.ByteArrayOutputStream()){for(String asset:new String[]{"channel-link.js","mobile-ui.js","mobile-clipboard.js","mobile-system-bars.js","mobile-session.js"}){try(java.io.InputStream in=assets.open(asset)){byte[] data=new byte[4096];int size;while((size=in.read(data))!=-1)out.write(data,0,size);}}return out.toString("UTF-8");}catch(java.io.IOException e){throw new IllegalStateException("Missing mobile layout",e);}}
}
