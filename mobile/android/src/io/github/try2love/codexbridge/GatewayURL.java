package io.github.try2love.codexbridge;
import java.net.URI;
import java.net.URLEncoder;

/** Origins are explicit user choices; credentials never move to another origin. */
public final class GatewayURL {
    public static String origin(String value) throws Exception {
        URI u=new URI(value.trim());String host=u.getHost();
        if(host==null||u.getRawUserInfo()!=null||u.getRawQuery()!=null||u.getPort()>65535||u.getPort()==0)throw new Exception("请填写完整网关地址，不含账号或参数");
        String scheme=u.getScheme()==null?"":u.getScheme().toLowerCase();
        if(!scheme.equals("https")&&!(scheme.equals("http")&&local(host)))throw new Exception("公网地址必须使用 HTTPS；HTTP 仅限局域网");
        if(u.getPath()!=null&&!u.getPath().isEmpty()&&!u.getPath().equals("/"))throw new Exception("网关地址不应包含路径");
        return scheme+"://"+u.getRawAuthority().toLowerCase();
    }
    static boolean local(String host){
        if(host.equalsIgnoreCase("localhost")||host.equals("[::1]"))return true;
        String[] p=host.split("\\.");if(p.length!=4)return false;
        int[] n=new int[4];try{for(int i=0;i<4;i++){n[i]=Integer.parseInt(p[i]);if(n[i]<0||n[i]>255||!p[i].equals(Integer.toString(n[i])))return false;}}catch(Exception e){return false;}
        return n[0]==10||n[0]==127||n[0]==192&&n[1]==168||n[0]==172&&n[1]>=16&&n[1]<=31;
    }
    public static String connection(String value)throws Exception {
        String base=origin(value);String fragment=new URI(value.trim()).getRawFragment();
        if(fragment!=null&&!fragment.isEmpty()&&!fragment.matches("pair=[A-Za-z0-9_-]{43}"))throw new Exception("请使用电脑网关提供的地址或二维码");
        return base+"/"+(fragment==null?"":"#"+fragment);
    }
    public static boolean sameOrigin(String url,String origin){try{URI u=new URI(url);return u.getRawUserInfo()==null&&(u.getScheme()+"://"+u.getRawAuthority()).equals(origin);}catch(Exception e){return false;}}
    public static String chat(String origin,String thread,String host)throws Exception {
        if(!thread.matches("[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")||host.length()>256)throw new Exception("聊天链接无效");
        return origin+"/#"+thread+"~"+URLEncoder.encode(host,"UTF-8").replace("+","%20");
    }
}
