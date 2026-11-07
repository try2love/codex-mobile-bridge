package io.github.try2love.codexbridge;

import java.io.IOException;
import java.net.URI;
import java.util.Collections;
import java.util.Map;

/** Per-view loopback authorization, never a persisted login or a gateway Cookie. */
final class CloudDownloadGrant implements ArtifactDownload.RequestHeaders {
 private final String origin,capability;
 private volatile boolean active=true;
 CloudDownloadGrant(String entry)throws IOException {
  try{
   URI value=new URI(entry);String fragment=value.getRawFragment();
   if(!"http".equals(value.getScheme())||!"127.0.0.1".equals(value.getHost())||value.getPort()<1||value.getPort()>65535||
      value.getRawUserInfo()!=null||value.getRawQuery()!=null||!"/".equals(value.getRawPath())||
      fragment==null||!fragment.matches("cmb-cloud=[a-f0-9]{32}"))throw new IllegalArgumentException();
   origin=GatewayURL.targetBase(entry);capability=fragment.substring("cmb-cloud=".length());
  }catch(Exception invalid){throw new IOException("云连接无效，请重新连接电脑");}
 }
 public Map<String,String> read(String target)throws IOException {
  if(!active||target==null||target.contains("#")||!ArtifactDownload.accepts(target,origin))throw new IOException("云连接已关闭，请重新连接电脑后下载");
  return Collections.singletonMap("X-CMB-Cloud-View",capability);
 }
 void close(){active=false;}
}
