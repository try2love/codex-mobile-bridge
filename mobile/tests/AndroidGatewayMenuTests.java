package io.github.try2love.codexbridge.menutest;

import android.app.*;
import android.content.*;
import android.graphics.Bitmap;
import android.os.*;
import android.view.*;
import android.webkit.WebView;
import android.widget.*;
import java.io.*;
import java.lang.reflect.*;
import java.util.*;
import java.util.concurrent.*;

/** Runs only in an explicitly selected disposable emulator, with synthetic connections. */
public class AndroidGatewayMenuTests extends Instrumentation {
 Activity app; SharedPreferences prefs; File output;
 final String origin="http://127.0.0.1:18495";
 final String[] sources={origin,"http://127.0.0.1:18496","http://127.0.0.1:18497","http://127.0.0.1:18498"};
 @Override public void onCreate(Bundle args){super.onCreate(args);start();}
 Object field(Object target,String name){try{Field f=target.getClass().getDeclaredField(name);f.setAccessible(true);return f.get(target);}catch(Exception e){throw new RuntimeException(e);}}
 Object call(Object target,String name,Class<?>[] types,Object... args){try{Method m=target.getClass().getDeclaredMethod(name,types);m.setAccessible(true);return m.invoke(target,args);}catch(Exception e){throw new RuntimeException(e);}}
 void check(boolean value,String why){if(!value)throw new AssertionError(why);}
 WebView web(){return (WebView)field(app,"web");}
 PopupWindow popup(){return (PopupWindow)field(app,"gatewayPopup");}
 void settle()throws Exception{Thread.sleep(300);waitForIdleSync();}
 String js(String source)throws Exception{
  CountDownLatch latch=new CountDownLatch(1);String[] result={null};
  runOnMainSync(()->web().evaluateJavascript(source,v->{result[0]=v;latch.countDown();}));
  check(latch.await(5,TimeUnit.SECONDS),"WebView callback timeout");return result[0];
 }
 void menu()throws Exception{
  runOnMainSync(()->call(app,"gatewayMenu",new Class[]{View.class},field(app,"chatMenu")));settle();
  check(popup()!=null&&popup().isShowing(),"Menu is visible");
 }
 void back()throws Exception{runOnMainSync(()->call(app,"handleBack",new Class[]{}));settle();}
 void shot(String name)throws Exception{
  Bitmap image=getUiAutomation().takeScreenshot();
  try(FileOutputStream stream=new FileOutputStream(new File(output,name+".png"))){image.compress(Bitmap.CompressFormat.PNG,100,stream);}image.recycle();
 }
 View row(String title){
  LinearLayout content=(LinearLayout)field(popup(),"content");
  for(int i=0;i<content.getChildCount();i++){View item=content.getChildAt(i);if(item instanceof LinearLayout&&((TextView)((LinearLayout)item).getChildAt(1)).getText().toString().equals(title))return item;}
  throw new AssertionError("Missing menu action: "+title);
 }
 void theme(String value)throws Exception{runOnMainSync(()->prefs.edit().putString("settingsTheme",value).apply());settle();}
 @Override public void onStart(){Bundle result=new Bundle();try{
  Context target=getTargetContext();prefs=target.getSharedPreferences("bridge",0);
  prefs.edit().clear().putString("language","zh").putString("settingsTheme","dark").putBoolean("askedNotifications",true).putBoolean("alerts",false).putBoolean("backgroundNotifications",false).commit();
  app=startActivitySync(new Intent().setClassName(target.getPackageName(),"io.github.try2love.codexbridge.MainActivity").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));waitForIdleSync();
  output=new File(target.getExternalFilesDir(null),"gateway-menu-tests");output.mkdirs();
  runOnMainSync(()->call(app,"connect",new Class[]{String.class},origin+"/"));settle();
  // Local HTML avoids native agents, accounts and network services. Production native methods run unchanged.
  String html="<html><head><meta name='viewport' content='width=device-width, initial-scale=1'><style>:root{--raised:#272a32;--text:#e4e6ed;--muted:#a0a5b1;--line:#30333c;--surface:#202228}body{background:var(--surface);color:var(--text);font:16px sans-serif;padding:24px}button,textarea{font:inherit}</style></head><body><main id='app'><h2>聊天列表</h2><p>菜单回归测试会话</p><textarea id='draft'>未发送的测试草稿</textarea><button id='client-switch-toggle' aria-expanded='true'>应用</button></main><script>window.depth=2;window.backs=0;window.BridgeNavigation={back(){window.backs++;return true}};document.getElementById('client-switch-toggle').onclick=function(){this.setAttribute('aria-expanded',this.getAttribute('aria-expanded')==='true'?'false':'true')};document.addEventListener('bridge-native-theme',()=>{document.documentElement.dataset.nativeTheme=BridgeNativeTheme.theme});window.fixtureReady=true;</script></body></html>";
  runOnMainSync(()->web().loadDataWithBaseURL(origin+"/",html,"text/html","UTF-8",origin+"/"));
  for(int i=0;i<30&&!"true".equals(js("!!window.fixtureReady"));i++)Thread.sleep(100);
  check("true".equals(js("!!window.fixtureReady")),"Local page loaded");Object retained=web();
  js("localStorage.setItem('fixture-draft',document.getElementById('draft').value);document.cookie='fixture_binding=synthetic;path=/;max-age=86400';true");
  menu();for(int i=0;i<30&&(int)field(popup(),"surface")!=0xff272a32;i++)settle();check((int)field(popup(),"surface")==0xff272a32,"Page dark palette actual="+field(popup(),"surface")+" url="+js("location.href")+" origin="+field(app,"origin")+" pageOrigin="+field(app,"pageOrigin")+" css="+js("JSON.stringify(['--raised','--text','--muted','--line','--surface'].map(k=>getComputedStyle(document.documentElement).getPropertyValue(k)))"));shot("menu-dark");
  sendKeyDownUpSync(KeyEvent.KEYCODE_BACK);settle();check(popup()==null,"System Back closes native popup");check(js("window.backs").equals("0"),"Popup Back leaves underlying page alone");
  runOnMainSync(()->{call(app,"gatewayMenu",new Class[]{View.class},field(app,"chatMenu"));call(app,"handleBack",new Class[]{});});settle();check(popup()==null,"Back cancels pending palette and delayed show");
  theme("light");check(js("document.documentElement.dataset.nativeTheme").equals("\"light\""),"Native theme still reaches web");
  js("for(const [k,v] of Object.entries({'--raised':'#ffffff','--text':'#24262b','--muted':'#777d88','--line':'#e9ebef','--surface':'#f6f7f9'}))document.documentElement.style.setProperty(k,v);true");
  menu();check((int)field(popup(),"surface")==0xffffffff,"Page light palette");shot("menu-light");
  // The same menu instance must disappear on preference change, without replacing the WebView.
  theme("dark");check(popup()==null,"Theme change closes old palette");check(retained==web(),"Theme preserves WebView");check(js("document.getElementById('draft').value").equals("\"未发送的测试草稿\""),"Theme preserves draft");
  js("document.documentElement.style.setProperty('--raised','invalid');true");menu();
  Context themed=(Context)field(field(app,"ui"),"context");android.content.res.TypedArray colors=themed.obtainStyledAttributes(new int[]{android.R.attr.colorBackground});int expected=colors.getColor(0,0);colors.recycle();
  check((int)field(popup(),"surface")==expected,"Invalid page colors fall back to selected native dark theme");back();
  js("document.documentElement.style.setProperty('--raised','#123456');true");menu();check((int)field(popup(),"surface")==0xff123456,"Custom page palette overrides native choice");
  runOnMainSync(()->row("隐藏底部 APP 图标").performClick());settle();check(js("document.getElementById('client-switch-toggle').getAttribute('aria-expanded')").equals("\"false\""),"App icon action targets current page");menu();check(row("显示底部 APP 图标").isShown(),"Toggle label follows page state");back();
  js("const d=document.createElement('dialog');d.textContent='会话详情';d.addEventListener('cancel',e=>{if(window.depth>1){e.preventDefault();window.depth--}});document.body.append(d);d.showModal();true");
  back();check(js("window.depth===1 && !!document.querySelector('dialog[open]')").equals("true"),"First Back returns within the session menu");back();check(js("!document.querySelector('dialog[open]')").equals("true"),"Second Back closes the dialog");check(js("window.backs").equals("0"),"Dialog never falls through to chat navigation");
  menu();runOnMainSync(()->callActivityOnPause(app));settle();check(popup()==null,"Pause dismisses popup");runOnMainSync(()->callActivityOnResume(app));settle();
  menu();runOnMainSync(()->web().reload());settle();check(popup()==null,"Document navigation dismisses popup");
  runOnMainSync(()->web().loadDataWithBaseURL(origin+"/",html,"text/html","UTF-8",origin+"/"));settle();
  prefs.edit().putStringSet("origins",new HashSet<>(Arrays.asList(sources))).commit();
  for(int i=0;i<sources.length;i++)prefs.edit().putBoolean("notificationsMuted:"+sources[i],i>0).commit();
  menu();runOnMainSync(()->row("手机设置").performClick());settle();check(popup()==null,"Settings action closes popup");check(((Dialog)field(app,"settingsPage")).isShowing(),"Settings entry still works");
  for(int i=0;i<sources.length;i++)check(prefs.getBoolean("notificationsMuted:"+sources[i],false)==(i>0),"Per-connection notification preference retained");
  runOnMainSync(()->((Dialog)field(app,"settingsPage")).dismiss());settle();check(retained==web(),"Settings retains chat WebView");
  check(js("localStorage.getItem('fixture-draft')").equals("\"未发送的测试草稿\""),"Web storage draft retained");
  android.webkit.CookieManager.getInstance().flush();
  result.putString("result","PASS palette, fallback, native theme sync, pending/system Back, nested cancel, navigation/pause, app icons, settings and four notification preferences");
  result.putString("screenshots",output.toString());finish(Activity.RESULT_OK,result);
 }catch(Throwable e){result.putString("failure",android.util.Log.getStackTraceString(e));try{shot("failure");}catch(Exception ignored){}finish(Activity.RESULT_CANCELED,result);}}
}
