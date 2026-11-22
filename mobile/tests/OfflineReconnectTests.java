package io.github.try2love.codexbridge;

public class OfflineReconnectTests {
    private static void same(String actual,String expected){
        if(!actual.equals(expected))throw new AssertionError(actual+" != "+expected);
    }
    public static void main(String[] args)throws Exception{
        String base="https://gateway.example", id="00000000-0000-4000-8000-000000000073";
        String query="?mode=recent&indexOnly=1&limit=1";
        String[] codex=GatewayURL.offlineTarget(base,"codex","local",id);
        same(codex[0],base+"/#"+id+"~local");
        same(codex[1],"/api/sessions/"+id+"/sync"+query+"&host=local");
        String[] ssh=GatewayURL.offlineTarget(base,"codex","ssh:lab+test",id);
        same(ssh[0],base+"/#"+id+"~ssh%3Alab%2Btest");
        same(ssh[1],"/api/sessions/"+id+"/sync"+query+"&host=ssh%3Alab%2Btest");
        String claudeEntry="claude:0123456789abcdef0123456789abcdef";
        String[] claude=GatewayURL.offlineTarget(base,claudeEntry,"local","project+thread");
        same(claude[0],base+"/#project%2Bthread~desktop%3A"+claudeEntry.replace(":","%3A"));
        same(claude[1],"/api/desktop-sessions/claude/sync"+query+"&sessionId=project%2Bthread&entry="+claudeEntry.replace(":","%3A"));
        if(GatewayURL.isChatPage(base+"/?offlineAnchor=%7B%7D#"+id+"~local",base))throw new AssertionError("External anchor accepted");
    }
}
