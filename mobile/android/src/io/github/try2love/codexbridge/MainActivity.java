package io.github.try2love.codexbridge;
import android.Manifest;
import android.app.*;
import android.content.*;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.*;
import android.view.*;
import android.webkit.*;
import android.widget.*;
import org.json.*;
import java.util.*;
import java.util.concurrent.*;

public final class MainActivity extends Activity implements SharedPreferences.OnSharedPreferenceChangeListener {
 private GatewayMenu gatewayPopup;
 private LinearLayout root,bar;private WebView web;private String origin="";private TextView status;private SystemBars systemBars;
 private android.content.SharedPreferences prefs;private final ExecutorService worker=Executors.newSingleThreadExecutor();
 private final ExecutorService probes=Executors.newFixedThreadPool(3);
 private Future<?> connectionTask;private Runnable connectionDeadline;
 private final Handler homeHandler=new Handler(Looper.getMainLooper());
 private final Map<String,TextView> homeStates=new LinkedHashMap<>();
 private final Map<String,LinearLayout> homeNotifications=new LinkedHashMap<>();
 private final Map<String,android.graphics.Bitmap> clientIcons=new HashMap<>();
 private final List<GatewayProbe> homeProbes=new ArrayList<>();
 private final List<Future<?>> probeTasks=new ArrayList<>();
 private int probeGeneration;private boolean resumed;
 private ConnectionDiscovery routeDiscovery;private boolean routeAttempted;private String pendingStorage;
 private final Runnable homeRefresh=()->checkComputers();
 private long homeBackAt;private Toast exitToast;
 private final ExecutorService downloads=Executors.newSingleThreadExecutor();
 private FrameLayout screen;private DownloadCard downloadCard;private ArtifactDownload.Task downloadTask,choosingDownload;private UpdateInstallSession updateSession;private boolean savingDownload,openingDownload,restoringUpdate,autoInstallUpdate;
 private String choosingUpdateFile;private Bundle pendingSaveResult;
 private GatewayUpdate preparingUpdate;
 private ServiceConnection monitor;private ValueCallback<Uri[]> files;private WebView fileView;private int fileGeneration;private int generation;private int navigationRequest;private boolean authLossAttempted;
 private SettingsPage settingsPage;private SettingsUi ui;private ImageButton chatMenu;
 private int pageColor(){return ui.dark?ui.page:0xfff5f5f3;}
 private int textColor(){return ui.dark?ui.text:0xff202124;}
 private int mutedColor(){return ui.dark?ui.muted:0xff777b80;}
 private int surfaceColor(){return ui.dark?ui.surface:0xffffffff;}
 private int buttonColor(){return ui.dark?ui.pressed:0xfff4f5f7;}
 private void syncWebTheme(){if(web!=null&&GatewayURL.sameOrigin(web.getUrl(),origin))web.evaluateJavascript("window.BridgeNativeTheme={theme:"+JSONObject.quote(SettingsUi.theme(this))+",dark:"+ui.dark+"};document.dispatchEvent(new Event(\"bridge-native-theme\"));",null);}
 private void applyTheme(){
  dismissGatewayMenu();
  ui=new SettingsUi(SettingsUi.themed(this));root.setBackgroundColor(pageColor());status.setTextColor(mutedColor());
  if(web==null)home();else{web.setBackgroundColor(pageColor());if(chatMenu!=null)chatMenu.setColorFilter(textColor());syncWebTheme();}
 }
 @Override public void onSharedPreferenceChanged(SharedPreferences preferences,String key){if("settingsTheme".equals(key))applyTheme();}
 String L(String value){return MobileStrings.text(this,value);}
 int dp(int n){return (int)(getResources().getDisplayMetrics().density*n);}
 TextView text(String value,int size){TextView t=new TextView(ui.context);t.setText(value);t.setTextSize(size);t.setTextColor(textColor());t.setPadding(0,dp(5),0,dp(5));return t;}
 android.graphics.drawable.GradientDrawable background(int color,int radius){android.graphics.drawable.GradientDrawable d=new android.graphics.drawable.GradientDrawable();d.setColor(color);d.setCornerRadius(dp(radius));return d;}
 Button button(String value,Runnable action){Button b=new Button(ui.context);b.setText(value);b.setTextSize(16);b.setTextColor(textColor());b.setAllCaps(false);b.setMinHeight(dp(54));b.setStateListAnimator(null);b.setElevation(0);b.setTranslationZ(0);b.setPadding(dp(18),dp(10),dp(18),dp(10));android.graphics.drawable.GradientDrawable face=background(buttonColor(),12);b.setBackground(new android.graphics.drawable.RippleDrawable(android.content.res.ColorStateList.valueOf(ui.dark?ui.pressed:0x18202124),face,null));b.setOnClickListener(v->action.run());return b;}
 LinearLayout column(){LinearLayout c=new LinearLayout(ui.context);c.setOrientation(1);return c;}
 void add(View v,LinearLayout parent,int bottom){LinearLayout.LayoutParams p=new LinearLayout.LayoutParams(-1,-2);p.bottomMargin=dp(bottom);parent.addView(v,p);}
 LinearLayout card(){LinearLayout c=column();c.setPadding(dp(20),dp(16),dp(20),dp(16));c.setBackground(background(surfaceColor(),22));return c;}
 ImageButton icon(String name,String description,Runnable action){ImageButton b=new ImageButton(ui.context);b.setImageResource(getResources().getIdentifier(name,"drawable",getPackageName()));b.setColorFilter(textColor());b.setContentDescription(description);b.setPadding(dp(12),dp(12),dp(12),dp(12));android.util.TypedValue ripple=new android.util.TypedValue();ui.context.getTheme().resolveAttribute(android.R.attr.selectableItemBackgroundBorderless,ripple,true);b.setBackgroundResource(ripple.resourceId);b.setOnClickListener(v->action.run());return b;}
 void header(boolean chat){bar.setVisibility(chat?View.GONE:View.VISIBLE);if(chat)return;bar.removeAllViews();ImageView logo=new ImageView(ui.context);logo.setImageResource(getResources().getIdentifier("ic_bridge","drawable",getPackageName()));LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(dp(30),dp(30));lp.setMarginEnd(dp(8));bar.addView(logo,lp);TextView title=text("Codex Bridge",18);title.setTypeface(null,android.graphics.Typeface.BOLD);bar.addView(title,new LinearLayout.LayoutParams(0,-2,1));bar.addView(icon("ic_bell",L("通知收件箱"),this::inbox),new LinearLayout.LayoutParams(dp(48),dp(48)));bar.addView(icon("ic_settings",L("手机设置"),this::settings),new LinearLayout.LayoutParams(dp(48),dp(48)));}
 void webAction(String selector){if(web!=null&&GatewayURL.sameOrigin(web.getUrl(),origin))web.evaluateJavascript("document.querySelector("+JSONObject.quote(selector)+")?.click()",null);}
 void rememberConnection(){
  final String address=origin;final int ticket=generation;final String snapshot=CookieManager.getInstance().getCookie(address+"/");
  worker.execute(()->{try{JSONObject auth=EventClient.request(address,"/api/auth",null,null);String name=auth.optString("cookieName","codex_mobile_session");String binding=BindingSelection.cookie(CookieManager.getInstance().getCookie(address+"/"),name);JSONObject data=ConnectionDiscovery.descriptor(auth,binding);
   runOnUiThread(()->{String current=BindingSelection.match(snapshot,CookieManager.getInstance().getCookie(address+"/"),name);
    if(ticket!=generation||!saved().contains(address)||current.isEmpty()||!binding.equals(current))return;
    if(auth.optBoolean("authenticated"))prefs.edit().putString("cookieName:"+address,name).apply();
    if(auth.optBoolean("authenticated")&&auth.optBoolean("trustedDevice")&&DeviceBinding.name(name))try{DeviceBinding.save(this,address,name,EventClient.tokenValue(current));}catch(Exception ignored){}
    else if(!auth.optBoolean("authenticated"))DeviceBinding.clear(this,address);
    if(data!=null)prefs.edit().putString("connection:"+address,data.toString()).apply();else prefs.edit().remove("connection:"+address).apply();});
  }catch(Exception ignored){}});
 }
 void loadWithRecovery(String address,String url,WebView current,int ticket){
  if(ticket!=generation||web!=current||!address.equals(origin)||!saved().contains(address))return;
  final String before=EventClient.sessionCookie(CookieManager.getInstance().getCookie(address+"/"),address);
  if(connectionTask!=null)connectionTask.cancel(true);
  final DeviceBinding.Credential saved=DeviceBinding.load(this,address);
  if(saved==null||saved.cookie().equals(before)){current.loadUrl(url);return;}
  connectionTask=worker.submit(()->{
   if(ticket!=generation||Thread.currentThread().isInterrupted())return;
   boolean recover=false;
   if(saved!=null&&!saved.cookie().equals(before)){
    Boolean existing=before.isEmpty()?false:DeviceBinding.verify(address,new DeviceBinding.Credential(before.substring(0,before.indexOf('=')),before.substring(before.indexOf('=')+1)));
    if(Boolean.FALSE.equals(existing)){
     Boolean valid=DeviceBinding.verify(address,saved);
     if(Boolean.TRUE.equals(valid))recover=true;
     else if(Boolean.FALSE.equals(valid)){DeviceBinding.Credential latest=DeviceBinding.load(this,address);if(latest!=null&&latest.cookie().equals(saved.cookie()))DeviceBinding.clear(this,address);}
    }
   }
   final boolean restore=recover;runOnUiThread(()->{
    if(ticket!=generation||web!=current||!address.equals(origin)||!saved().contains(address))return;
    DeviceBinding.Credential latest=DeviceBinding.load(this,address);
    if(!restore||latest==null||!latest.cookie().equals(saved.cookie())||!before.equals(EventClient.sessionCookie(CookieManager.getInstance().getCookie(address+"/"),address))){current.loadUrl(url);return;}
    EventClient.setName(address,saved.name);
    CookieManager.getInstance().setCookie(address+"/",saved.cookie()+"; Path="+GatewayURL.cookiePath(address)+"; HttpOnly; SameSite=Strict; Max-Age=34560000"+(address.startsWith("https://")?"; Secure":""),ok->{
     DeviceBinding.Credential retained=DeviceBinding.load(this,address);
     if(ticket!=generation||web!=current||!address.equals(origin)||!saved().contains(address)||retained==null||!retained.cookie().equals(saved.cookie())){if(ok&&CookieManager.getInstance().getCookie(address+"/")!=null&&CookieManager.getInstance().getCookie(address+"/").contains(saved.cookie()))CookieManager.getInstance().setCookie(address+"/",saved.name+"=; Path="+GatewayURL.cookiePath(address)+"; Max-Age=0");return;}
     if(ok){prefs.edit().putString("cookieName:"+address,saved.name).apply();CookieManager.getInstance().flush();MonitorService.bindingsChanged();}
     current.loadUrl(url);
    });
   });
  });
 }
 void recoverConnection(String requested){
  if(routeAttempted||!resumed||web==null)return;routeAttempted=true;
  final String address=origin,encoded=prefs.getString("connection:"+address,null),cookie=EventClient.sessionCookie(CookieManager.getInstance().getCookie(address+"/"),address);final int ticket=generation;
  if(encoded==null||cookie.isEmpty())return;
  final ConnectionDiscovery resolver=new ConnectionDiscovery();routeDiscovery=resolver;
  worker.execute(()->{try{String next=resolver.resolve(address,new JSONObject(encoded),cookie);runOnUiThread(()->{
   if(ticket!=generation||!resumed||routeDiscovery!=resolver||next.equals(address)||!saved().contains(address)||!cookie.equals(EventClient.sessionCookie(CookieManager.getInstance().getCookie(address+"/"),address)))return;
   final String[] snapshot={null};
   Runnable relocate=()->{String existing=EventClient.sessionCookie(CookieManager.getInstance().getCookie(next+"/"),next);if(!existing.isEmpty()&&!existing.equals(cookie))return;if(ticket!=generation||!resumed||routeDiscovery!=resolver||!saved().contains(address)||!cookie.equals(EventClient.sessionCookie(CookieManager.getInstance().getCookie(address+"/"),address)))return;
    CookieManager.getInstance().setCookie(next+"/",cookie+"; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=34560000",ok->{if(!ok||ticket!=generation||!resumed||routeDiscovery!=resolver||!saved().contains(address)||!cookie.equals(EventClient.sessionCookie(CookieManager.getInstance().getCookie(address+"/"),address)))return;
     if(!saved().contains(next))NotificationSources.setEnabled(prefs,next,NotificationSources.enabled(prefs,address));
     Set<String> values=saved();values.remove(address);values.add(next);android.content.SharedPreferences.Editor edit=prefs.edit().putStringSet("origins",values).putString("connection:"+next,encoded).remove("connection:"+address).remove("notificationsMuted:"+address).remove("notificationSourceRevision:"+address);
     for(String prefix:new String[]{"name:","computer-name:","notificationCheckpoint:","last-opened:","cookieName:"}){Object value=prefs.getAll().get(prefix+address);if(value instanceof String)edit.putString(prefix+next,(String)value);else if(value instanceof Long)edit.putLong(prefix+next,(Long)value);edit.remove(prefix+address);}edit.apply();
     DeviceBinding.Credential backup=DeviceBinding.load(MainActivity.this,address);if(backup!=null&&backup.cookie().equals(cookie)&&DeviceBinding.load(MainActivity.this,next)==null)try{DeviceBinding.save(MainActivity.this,next,backup.name,backup.token);DeviceBinding.clear(MainActivity.this,address);}catch(Exception ignored){}EventClient.setName(next,EventClient.cookieName(address));EventClient.forget(address);connect(next+requested.substring(address.length()));pendingStorage=snapshot[0];routeAttempted=true;
    });};
   if(web!=null&&GatewayURL.sameOrigin(web.getUrl(),address))web.evaluateJavascript("(()=>{if(typeof saveDraft==='function')saveDraft();return JSON.stringify(Object.fromEntries(Object.entries(localStorage)));})()",value->{if(ticket!=generation)return;try{Object parsed=new org.json.JSONTokener(value).nextValue();if(parsed instanceof String&&((String)parsed).length()<524288){JSONObject data=new JSONObject((String)parsed);snapshot[0]=data.toString();}}catch(Exception ignored){}relocate.run();});else relocate.run();
  });}catch(Exception ignored){}});
 }
 void webAccounts(){if(web!=null&&GatewayURL.sameOrigin(web.getUrl(),origin))web.evaluateJavascript("(()=>{if(typeof window.BridgeNavigation?.accounts==='function')return window.BridgeNavigation.accounts();document.getElementById('accounts-button')?.click();return true;})()",null);}
 void dismissGatewayMenu(){GatewayMenu popup=gatewayPopup;gatewayPopup=null;if(popup!=null)popup.dismiss();}
 void gatewayMenu(View anchor){
  if(web==null||!anchor.isShown()||!anchor.isEnabled())return;
  dismissGatewayMenu();
  final WebView current=web;final String address=origin;final int ticket=generation;
  GatewayMenu menu=new GatewayMenu(ui.context);gatewayPopup=menu;
  menu.setOnDismissListener(()->{if(gatewayPopup==menu)gatewayPopup=null;});
  menu.add(L("返回电脑列表"),"ic_menu_monitor",this::home);
  menu.separator();
  menu.add(L("账号与接入"),"ic_menu_user_round",this::webAccounts);
  menu.add(L("通知收件箱"),"ic_menu_bell",this::inbox);
  menu.separator();
  menu.add(L("外观与显示"),"ic_menu_palette",()->webAction("[data-open-appearance]"));
  menu.add(L("手机设置"),"ic_menu_settings",this::settings);
  menu.separator();
  menu.add(L("刷新页面"),"ic_menu_refresh_cw",()->{if(web==current&&generation==ticket){routeAttempted=false;current.reload();}});
  final LinearLayout appIcons=menu.add(L("隐藏底部 APP 图标"),"ic_menu_eye_off",()->{if(web==current&&generation==ticket)webAction("#client-switch-toggle");});
  appIcons.setVisibility(View.GONE);
  current.evaluateJavascript("(()=>{const button=document.getElementById('client-switch-toggle');return document.getElementById('app')?.hidden===false&&button&&!button.hidden?button.getAttribute('aria-expanded')==='false':null;})()",value->{
   if(web!=current||generation!=ticket||gatewayPopup!=menu||!GatewayURL.sameOrigin(current.getUrl(),address))return;
   if("true".equals(value)||"false".equals(value)){
    boolean hidden="true".equals(value);
    menu.update(appIcons,L(hidden?"显示底部 APP 图标":"隐藏底部 APP 图标"),hidden?"ic_menu_eye":"ic_menu_eye_off");
    appIcons.setVisibility(View.VISIBLE);menu.resize(anchor);
   }
  });
  current.evaluateJavascript("(()=>{const style=getComputedStyle(document.documentElement),color=name=>style.getPropertyValue(name).trim();return {surface:color('--raised'),text:color('--text'),muted:color('--muted'),line:color('--line'),pressed:color('--surface')};})()",value->{
   if(web!=current||generation!=ticket||gatewayPopup!=menu||!anchor.isShown()||!anchor.isEnabled()||!GatewayURL.sameOrigin(current.getUrl(),address))return;
   menu.palette(value);if(!menu.isShowing())menu.show(anchor);
  });
  anchor.postDelayed(()->{if(web==current&&generation==ticket&&gatewayPopup==menu&&anchor.isShown()&&anchor.isEnabled()&&!menu.isShowing())menu.show(anchor);},120);
 }

 void message(String value){resetExitGesture();new AlertDialog.Builder(ui.context).setMessage(L(value)).setPositiveButton(L("好"),null).show();}
 Set<String> saved(){return new LinkedHashSet<>(prefs.getStringSet("origins",new HashSet<>()));}
 @Override public void onCreate(Bundle state){super.onCreate(state);prefs=getSharedPreferences("bridge",0);ui=new SettingsUi(SettingsUi.themed(this));prefs.registerOnSharedPreferenceChangeListener(this);for(String address:prefs.getStringSet("origins",java.util.Collections.emptySet()))EventClient.setName(address,prefs.getString("cookieName:"+address,null));root=new LinearLayout(ui.context);root.setOrientation(1);root.setBackgroundColor(pageColor());screen=new FrameLayout(this);screen.addView(root,new FrameLayout.LayoutParams(-1,-1));setContentView(screen);systemBars=new SystemBars(this,screen,()->{if(downloadCard!=null)downloadCard.post(downloadCard::place);});
  if(state!=null){choosingUpdateFile=state.getString("choosingUpdateFile");pendingSaveResult=state.getBundle("pendingSaveResult");}
  if(Build.VERSION.SDK_INT>=33)getOnBackInvokedDispatcher().registerOnBackInvokedCallback(android.window.OnBackInvokedDispatcher.PRIORITY_DEFAULT,this::handleBack);
  bar=new LinearLayout(ui.context);bar.setGravity(Gravity.CENTER_VERTICAL);bar.setPadding(dp(20),dp(4),dp(12),dp(4));root.addView(bar);header(false);
  downloadCard=new DownloadCard(this,screen,this::downloadAction,this::openDownload,this::cancelDownload);
  status=text("",12);status.setTextColor(mutedColor());status.setGravity(Gravity.CENTER);root.addView(status);
  if(!openIntent(getIntent())){String active=prefs.getString("active","");if(saved().contains(active))connect(active+"/");else home();}monitor();root.post(this::requestStartupNotifications);restoreUpdate();
  if(state!=null&&state.getBoolean("settingsOpen",false)){int offset=state.getInt("settingsScroll",0);root.post(()->{settingsPage=new SettingsPage(this,offset,false);settingsPage.show();});}
 }
 void cancelConnectionDeadline(){if(connectionDeadline!=null){homeHandler.removeCallbacks(connectionDeadline);connectionDeadline=null;}}
 void watchConnection(String url,WebView current,int ticket){
  cancelConnectionDeadline();connectionDeadline=()->{
   if(ticket!=generation||web!=current)return;
   connectionDeadline=null;status.setVisibility(View.VISIBLE);status.setText(L("连接失败，请在更多菜单中刷新"));recoverConnection(url);
  };homeHandler.postDelayed(connectionDeadline,12000);
 }
 void clear(){dismissGatewayMenu();cancelConnectionDeadline();if(connectionTask!=null){connectionTask.cancel(true);connectionTask=null;}if(preparingUpdate!=null){preparingUpdate.cancel();preparingUpdate=null;}pendingStorage=null;if(routeDiscovery!=null){routeDiscovery.cancel();routeDiscovery=null;}if(files!=null){files.onReceiveValue(null);files=null;fileView=null;}stopComputerChecks();homeStates.clear();homeNotifications.clear();resetExitGesture();generation++;if(web!=null){web.stopLoading();web.destroy();web=null;}systemBars.update(pageColor(),pageColor());while(root.getChildCount()>2)root.removeViewAt(2);}
 void home(){clear();header(false);status.setVisibility(View.GONE);ScrollView scroll=new ScrollView(ui.context);scroll.setFillViewport(true);LinearLayout content=column();content.setPadding(dp(20),dp(16),dp(20),dp(24));scroll.addView(content);root.addView(scroll,new LinearLayout.LayoutParams(-1,0,1));
  ImageView icon=new ImageView(ui.context);icon.setImageResource(getResources().getIdentifier("ic_computer","drawable",getPackageName()));icon.setColorFilter(textColor());icon.setScaleType(ImageView.ScaleType.FIT_CENTER);LinearLayout.LayoutParams ip=new LinearLayout.LayoutParams(dp(64),dp(58));ip.gravity=Gravity.CENTER;ip.bottomMargin=dp(24);content.addView(icon,ip);
  TextView headline=text(L("电脑上的工作，\n带在身边。"),30);headline.setTypeface(null,android.graphics.Typeface.BOLD);headline.setGravity(Gravity.CENTER);add(headline,content,10);
  TextView intro=text(L("继续聊天、查看结果，让电脑替你运行。"),15);intro.setTextColor(mutedColor());intro.setGravity(Gravity.CENTER);add(intro,content,26);
  LinearLayout connection=card();Button scan=button(L("扫码连接电脑"),this::scan);scan.setTextColor(surfaceColor());scan.setBackground(background(textColor(),16));add(scan,connection,8);add(button(L("输入网关地址"),this::manualAddress),connection,8);add(button("DSH Mobile",()->manualDsh()),connection,0);add(connection,content,24);
  LinearLayout sectionRow=new LinearLayout(ui.context);sectionRow.setGravity(Gravity.CENTER_VERTICAL);TextView section=text(L("我的电脑"),19);section.setTypeface(null,android.graphics.Typeface.BOLD);sectionRow.addView(section,new LinearLayout.LayoutParams(0,-2,1));sectionRow.addView(button(L("刷新状态"),()->{checkComputers();MonitorService.refresh();}));add(sectionRow,content,12);
  if(saved().isEmpty()&&DshMobileStore.ids(this).isEmpty()){LinearLayout empty=card();add(text(L("还没有连接的电脑"),17),empty,6);TextView hint=text(L("在电脑网关中展开“扫码登录”，然后用上方按钮扫描。"),14);hint.setTextColor(mutedColor());add(hint,empty,0);add(empty,content,18);}
  for(String entry:recentComputers()){
   if(entry.startsWith("dsh:")){add(dshComputer(entry.substring(4)),content,12);continue;}
   LinearLayout computer=card(),heading=new LinearLayout(ui.context);heading.setGravity(Gravity.CENTER_VERTICAL);
   TextView reachability=text(L("检查中…"),12);reachability.setTextColor(mutedColor());reachability.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);heading.addView(reachability);homeStates.put(entry,reachability);
   TextView name=text(computerName(entry),17);name.setTypeface(null,android.graphics.Typeface.BOLD);name.setSingleLine();name.setEllipsize(android.text.TextUtils.TruncateAt.END);name.setPadding(dp(10),dp(5),dp(8),dp(5));heading.addView(name,new LinearLayout.LayoutParams(0,-2,1));LinearLayout apps=new LinearLayout(ui.context);apps.setGravity(Gravity.CENTER_VERTICAL);heading.addView(apps);homeNotifications.put(entry,apps);add(heading,computer,4);
   TextView address=text(entry,13);address.setTextColor(mutedColor());add(address,computer,4);
   LinearLayout actions=new LinearLayout(ui.context);Button rename=button(L("重命名"),()->renameComputer(entry));Button remove=button(L("移除这台电脑"),()->confirmRemove(entry));remove.setTextColor(ui.dark?ui.danger:0xffb44235);
   LinearLayout.LayoutParams renameSize=new LinearLayout.LayoutParams(0,-2,1);renameSize.setMarginEnd(dp(8));actions.addView(rename,renameSize);actions.addView(remove,new LinearLayout.LayoutParams(0,-2,1));add(actions,computer,0);
   computer.setOnClickListener(v->connect(entry+"/"));computer.setContentDescription(L("连接 ")+computerName(entry));add(computer,content,12);
  }
  TextView foot=text(L("外出使用 HTTPS 地址；局域网地址需要连接同一网络。"),13);foot.setTextColor(mutedColor());foot.setGravity(Gravity.CENTER);add(foot,content,0);renderComputerNotifications();checkComputers();
 }
 List<String> recentComputers(){List<String> entries=new ArrayList<>(saved());for(String id:DshMobileStore.ids(this))entries.add("dsh:"+id);entries.sort((a,b)->{int order=Long.compare(prefs.getLong("last-opened:"+b,0),prefs.getLong("last-opened:"+a,0));return order!=0?order:a.compareTo(b);});return entries;}
 LinearLayout dshComputer(String id){
  String url=DshMobileStore.load(this,id);LinearLayout item=card();
  TextView name=text("DSH Mobile",17);name.setTypeface(null,android.graphics.Typeface.BOLD);add(name,item,4);
  String address=L("连接凭据不可用");try{address=DshMobileURL.display(url);}catch(Exception ignored){}
  TextView detail=text(address,13);detail.setTextColor(mutedColor());add(detail,item,4);
  Runnable connect=()->{if(url==null)message(L("连接凭据不可用，请移除后重新添加"));else openDsh(id,url);};
  LinearLayout actions=new LinearLayout(ui.context);Button open=button(L("连接"),connect);
  Button remove=button(L("移除这台电脑"),()->new AlertDialog.Builder(ui.context).setMessage(L("移除这台电脑")+"？").setNegativeButton(L("取消"),null).setPositiveButton(L("移除"),(d,w)->{DshMobileStore.remove(this,id);prefs.edit().remove("last-opened:dsh:"+id).apply();home();}).show());remove.setTextColor(ui.dark?ui.danger:0xffb44235);
  LinearLayout.LayoutParams size=new LinearLayout.LayoutParams(0,-1,1);size.setMarginEnd(dp(8));actions.addView(open,size);actions.addView(remove,new LinearLayout.LayoutParams(0,-1,1));add(actions,item,0);
  item.setOnClickListener(v->connect.run());item.setContentDescription(L("连接 ")+"DSH Mobile · "+address);return item;
 }
 String computerName(String address){return prefs.getString("name:"+address,Uri.parse(address).getHost());}
 void renameComputer(String address){
  EditText field=new EditText(ui.context);field.setSingleLine();field.setFilters(new android.text.InputFilter[]{new android.text.InputFilter.LengthFilter(80)});field.setText(computerName(address));field.selectAll();field.setPadding(dp(24),dp(16),dp(24),dp(16));
  new AlertDialog.Builder(ui.context).setTitle(L("重命名电脑")).setMessage(address+"\n"+L("留空恢复默认名称")).setView(field).setNegativeButton(L("取消"),null).setPositiveButton(L("保存"),(d,w)->{String name=field.getText().toString().trim();android.content.SharedPreferences.Editor edit=prefs.edit();if(name.isEmpty())edit.remove("name:"+address);else edit.putString("name:"+address,name);edit.apply();home();}).show();
 }
 void renderComputerNotifications(){
  if(!resumed||web!=null)return;
  for(Map.Entry<String,LinearLayout> entry:homeNotifications.entrySet()){
   LinearLayout apps=entry.getValue();apps.removeAllViews();JSONArray rows=ComputerNotifications.clients(this,entry.getKey());
   for(int i=0;i<rows.length();i++){
    JSONObject row=rows.optJSONObject(i);if(row==null||!row.optBoolean("enabled"))continue;
    String provider=row.optString("id"),name=provider.equals("codex")?"Codex":provider.equals("claude")?"Claude":provider.equals("deepseek")?"DeepSeek Harness":provider.equals("zcode")?"ZCode":"";if(name.isEmpty())continue;
    long count=Math.max(0,row.optLong("unread"));FrameLayout slot=new FrameLayout(this);ImageButton app=new ImageButton(ui.context);app.setBackgroundColor(android.graphics.Color.TRANSPARENT);app.setScaleType(ImageView.ScaleType.FIT_CENTER);app.setPadding(dp(11),dp(11),dp(11),dp(11));
    if(!clientIcons.containsKey(provider))try(java.io.InputStream input=getAssets().open(provider+".png")){clientIcons.put(provider,android.graphics.BitmapFactory.decodeStream(input));}catch(java.io.IOException ignored){}
    app.setImageBitmap(clientIcons.get(provider));app.setContentDescription(name+(row.has("connected")&&!row.isNull("connected")&&!row.optBoolean("connected")?" · "+L("尚未连接"):"")+" · "+L("未读通知")+" "+count);app.setOnClickListener(v->connect(entry.getKey()+"/?client="+provider));slot.addView(app,new FrameLayout.LayoutParams(dp(48),dp(48)));
    if(count>0){TextView badge=text(count>99?"99+":String.valueOf(count),10);badge.setPadding(dp(4),0,dp(4),0);badge.setMinWidth(dp(18));badge.setGravity(Gravity.CENTER);badge.setTextColor(0xffffffff);badge.setBackground(background(0xffcf3939,10));badge.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);slot.addView(badge,new FrameLayout.LayoutParams(-2,dp(18),Gravity.TOP|Gravity.START));}
    slot.setAlpha(ComputerNotifications.fresh(entry.getKey())&&(!row.has("connected")||row.isNull("connected")||row.optBoolean("connected"))?1f:.5f);apps.addView(slot,new LinearLayout.LayoutParams(dp(48),dp(48)));
   }
  }
 }
 void stopComputerChecks(){probeGeneration++;homeHandler.removeCallbacks(homeRefresh);for(GatewayProbe probe:homeProbes)probe.cancel();homeProbes.clear();for(Future<?> task:probeTasks)task.cancel(true);probeTasks.clear();}
 void checkComputers(){
  stopComputerChecks();if(!resumed||web!=null||homeStates.isEmpty())return;final int ticket=probeGeneration;
  for(Map.Entry<String,TextView> entry:homeStates.entrySet()){
   entry.getValue().setText(L("检查中…"));entry.getValue().setTextColor(mutedColor());GatewayProbe probe=new GatewayProbe();homeProbes.add(probe);
   probeTasks.add(probes.submit(()->{boolean online=probe.check(entry.getKey());runOnUiThread(()->{if(ticket!=probeGeneration||!resumed||web!=null)return;TextView label=entry.getValue();label.setText((online?"● ":"○ ")+L(online?"在线":"暂不可达"));label.setTextColor(online?(ui.dark?0xff73cda6:0xff23815b):mutedColor());});}));
  }
  homeHandler.postDelayed(homeRefresh,30000);
 }
 void manualAddress(){resetExitGesture();EditText field=new EditText(ui.context);field.setSingleLine();field.setInputType(17);field.setText("https://");field.setSelection(field.length());field.setPadding(dp(24),dp(16),dp(24),dp(16));new AlertDialog.Builder(ui.context).setTitle(L("连接电脑")).setMessage(L("粘贴电脑网关提供的访问地址")).setView(field).setPositiveButton(L("继续"),(d,w)->choose(field.getText().toString())).setNegativeButton(L("取消"),null).show();}
 boolean htmlPreviewShell(String target){return target!=null&&target.equals(origin+"/html-preview-shell");}
 void manualDsh(){EditText field=new EditText(ui.context);field.setSingleLine();field.setInputType(17);field.setHint("https://…/?auth=…");new AlertDialog.Builder(ui.context).setTitle("DSH Mobile").setMessage(L("粘贴 DSH 客户端 Mobile 功能提供的完整链接")).setView(field).setPositiveButton(L("继续"),(d,w)->chooseDsh(field.getText().toString())).setNegativeButton(L("取消"),null).show();}
 void chooseDsh(String input){try{String url=DshMobileURL.connection(input);new AlertDialog.Builder(ui.context).setTitle(L("连接 DSH Mobile？")).setMessage(DshMobileURL.display(url)+L("\n请确认这是你自己的 DSH 客户端。将打开它自带的手机界面。")).setPositiveButton(L("连接"),(d,w)->{try{String id=DshMobileStore.save(this,url);openDsh(id,url);}catch(Exception invalid){message(L("无法保存 DSH Mobile 连接"));}}).setNegativeButton(L("取消"),null).show();}catch(Exception invalid){message(invalid.getMessage());}}
 void openDsh(String id,String url){prefs.edit().putLong("last-opened:dsh:"+id,System.currentTimeMillis()).apply();home();if(Build.VERSION.SDK_INT<28){new AlertDialog.Builder(ui.context).setMessage(L("Android 8 请使用浏览器打开 DSH Mobile；App 内连接需要 Android 9 或更高版本。")).setPositiveButton(L("打开"),(d,w)->startActivity(new Intent(Intent.ACTION_VIEW,Uri.parse(url)))).setNegativeButton(L("取消"),null).show();return;}startActivity(new Intent(this,DshMobileActivity.class).putExtra("url",url).putExtra("theme",SettingsUi.theme(this)));}
 void choose(String value){resetExitGesture();try{String target=GatewayURL.connection(value);String base=GatewayURL.origin(value);new AlertDialog.Builder(ui.context).setTitle(L("连接到这台电脑？")).setMessage(base+L("\n请确认这是你自己的网关。")).setPositiveButton(L("连接"),(d,w)->connect(target)).setNeutralButton("DSH Mobile",(d,w)->chooseDsh(value)).setNegativeButton(L("取消"),null).show();}catch(Exception e){chooseDsh(value);}}

 void connect(String url){routeAttempted=false;try{String base=GatewayURL.targetBase(url);clear();header(true);status.setVisibility(View.VISIBLE);origin=base;Set<String> saved=saved();saved.add(origin);prefs.edit().putStringSet("origins",saved).putString("active",origin).putLong("last-opened:"+origin,System.currentTimeMillis()).apply();status.setText(L("正在连接 · ")+origin);
  FrameLayout viewport=new FrameLayout(this);root.addView(viewport,new LinearLayout.LayoutParams(-1,0,1));web=new WebView(ui.context);web.setBackgroundColor(pageColor());viewport.addView(web,new FrameLayout.LayoutParams(-1,-1));ImageButton menu=icon("ic_more",L("电脑与聊天选项"),()->{});chatMenu=menu;menu.setOnClickListener(v->gatewayMenu(v));FrameLayout.LayoutParams menuPosition=new FrameLayout.LayoutParams(dp(48),dp(48),Gravity.TOP|Gravity.END);menuPosition.topMargin=dp(4);menuPosition.setMarginEnd(dp(8));viewport.addView(menu,menuPosition);final String clipboardToken=UUID.randomUUID().toString();final String appearance=MobileWebScripts.appearance(getAssets()).replace("__BRIDGE_CLIPBOARD_TOKEN__",clipboardToken).replace("'__BRIDGE_CONNECTION_NAME__'",JSONObject.quote(computerName(origin))).replace("'__BRIDGE_NATIVE_LANGUAGE__'",JSONObject.quote(prefs.getString("language",java.util.Locale.getDefault().getLanguage()).startsWith("en")?"en":"zh"));WebSettings s=web.getSettings();s.setJavaScriptEnabled(true);s.setDomStorageEnabled(true);s.setAllowFileAccess(false);s.setAllowContentAccess(true);s.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);s.setSupportMultipleWindows(false);s.setUserAgentString(s.getUserAgentString()+" BridgeMobile/0.1-Android");CookieManager.getInstance().setAcceptThirdPartyCookies(web,false);
  // Keep the WebView glyph size aligned with the terminal's 6px lower bound.
  s.setMinimumFontSize(6);s.setMinimumLogicalFontSize(6);
  web.setWebViewClient(new WebViewClient(){
   public void onPageStarted(WebView view,String target,android.graphics.Bitmap favicon){if(view==web)dismissGatewayMenu();}
   public boolean shouldOverrideUrlLoading(WebView view,WebResourceRequest request){String target=request.getUrl().toString();if(!request.isForMainFrame())return !htmlPreviewShell(target);if(htmlPreviewShell(target))return true;if(target.equals("codexbridge://home")&&request.hasGesture()&&GatewayURL.sameOrigin(view.getUrl(),origin)){home();return true;}if(ArtifactDownload.accepts(target,origin)&&request.hasGesture()){downloadArtifact(target);return true;}if(GatewayURL.sameOrigin(target,origin))return false;if(request.hasGesture()&&("https".equals(request.getUrl().getScheme())||"http".equals(request.getUrl().getScheme())))new AlertDialog.Builder(ui.context).setMessage(L("在浏览器打开外部链接？\n")+request.getUrl().getHost()).setPositiveButton(L("打开"),(d,w)->startActivity(new Intent(Intent.ACTION_VIEW,request.getUrl()))).setNegativeButton(L("取消"),null).show();return true;}
   public void onPageCommitVisible(WebView view,String target){if(view==web&&GatewayURL.sameOrigin(target,origin)){cancelConnectionDeadline();syncWebTheme();status.setVisibility(View.GONE);}}
   public void onPageFinished(WebView view,String target){if(view==web&&GatewayURL.sameOrigin(target,origin)){cancelConnectionDeadline();CookieManager.getInstance().flush();view.evaluateJavascript("window.BridgeMobileCapabilities = Object.freeze({ diagnosticsExport: true });",null);view.evaluateJavascript(appearance,null);syncWebTheme();status.setVisibility(View.GONE);registerPushToken();rememberConnection();
    if(pendingStorage!=null){String snapshot=pendingStorage;pendingStorage=null;view.evaluateJavascript("(()=>{const data="+snapshot+";if(data&&typeof data==='object'){for(const [k,v] of Object.entries(data))if(typeof v==='string')localStorage.setItem(k,v);location.reload();}})()",null);}}}
   public void onReceivedError(WebView view,WebResourceRequest req,WebResourceError e){if(view==web&&req.isForMainFrame()){cancelConnectionDeadline();status.setVisibility(View.VISIBLE);status.setText(L("连接失败，请在更多菜单中刷新"));recoverConnection(url);}}
   public void onReceivedHttpError(WebView view,WebResourceRequest req,WebResourceResponse response){if(view==web&&req.isForMainFrame()&&GatewayURL.sameOrigin(req.getUrl().toString(),origin)&&ConnectionDiscovery.retryableStatus(response.getStatusCode()))recoverConnection(url);}
  });
  web.setWebChromeClient(new WebChromeClient(){
   @Override public boolean onJsAlert(WebView view,String url,String message,JsResult result){if(view==web&&htmlPreviewShell(url)){result.cancel();return true;}return super.onJsAlert(view,url,message,result);}
   @Override public boolean onJsConfirm(WebView view,String url,String message,JsResult result){if(view==web&&htmlPreviewShell(url)){result.cancel();return true;}return super.onJsConfirm(view,url,message,result);}
   @Override public boolean onJsPrompt(WebView view,String url,String prompt,String value,JsPromptResult result){
    if(htmlPreviewShell(url)){result.cancel();return true;}
    if(prompt.equals("codexbridge-system-bars:"+clipboardToken)){
     if(view==web&&view.hasWindowFocus()&&GatewayURL.sameOrigin(url,origin)&&GatewayURL.sameOrigin(view.getUrl(),origin)&&value!=null&&value.length()<100){try{JSONObject colors=new JSONObject(value);String top=colors.getString("top"),bottom=colors.getString("bottom");if(top.matches("#[0-9a-fA-F]{6}")&&bottom.matches("#[0-9a-fA-F]{6}")){systemBars.update(android.graphics.Color.parseColor(top),android.graphics.Color.parseColor(bottom));result.confirm("saved");return true;}}catch(Exception ignored){}}
     result.cancel();return true;
    }
    if(prompt.equals("codexbridge-modal:"+clipboardToken)){
     if(view==web&&view.hasWindowFocus()&&GatewayURL.sameOrigin(url,origin)&&GatewayURL.sameOrigin(view.getUrl(),origin)&&("open".equals(value)||"closed".equals(value))){boolean open="open".equals(value);menu.setVisibility(open?View.GONE:View.VISIBLE);menu.setEnabled(!open);if(open)dismissGatewayMenu();result.confirm("saved");}else result.cancel();return true;
    }
    if(prompt.equals("codexbridge-session:"+clipboardToken)){
     if(view==web&&GatewayURL.sameOrigin(url,origin)&&GatewayURL.sameOrigin(view.getUrl(),origin)){authLossAttempted=false;CookieManager.getInstance().flush();MonitorService.bindingsChanged();rememberConnection();result.confirm("saved");}else result.cancel();return true;
    }
    if(prompt.equals("codexbridge-chat-visible:"+clipboardToken)){
     if(view==web&&resumed&&view.hasWindowFocus()&&saved().contains(origin)&&GatewayURL.sameOrigin(url,origin)&&GatewayURL.sameOrigin(view.getUrl(),origin)&&value!=null&&value.length()<=4096){
      try{JSONObject chat=new JSONObject(value);String thread=chat.getString("threadId"),host=chat.getString("host");GatewayURL.chat(origin,thread,host);TaskNotifications.clearChat(MainActivity.this,origin,thread,host);result.confirm("cleared");}catch(Exception e){result.cancel();}
     }else result.cancel();return true;
    }
    if(prompt.equals("codexbridge-auth-lost:"+clipboardToken)){
     if(view==web&&view.hasWindowFocus()&&GatewayURL.sameOrigin(url,origin)&&GatewayURL.sameOrigin(view.getUrl(),origin)&&!authLossAttempted){authLossAttempted=true;loadWithRecovery(origin,origin+"/",view,generation);result.confirm("recovering");}else result.cancel();return true;
    }
    if(prompt.equals("codexbridge-logout:"+clipboardToken)){
     if(view==web&&view.hasWindowFocus()&&GatewayURL.sameOrigin(url,origin)&&GatewayURL.sameOrigin(view.getUrl(),origin)){
      DeviceBinding.clear(MainActivity.this,origin);EventClient.forget(origin);MonitorService.bindingsChanged();result.confirm("cleared");
     }else result.cancel();return true;
    }
    if(prompt.equals("codexbridge-theme:"+clipboardToken)){
     if(view==web&&view.hasWindowFocus()&&GatewayURL.sameOrigin(url,origin)&&GatewayURL.sameOrigin(view.getUrl(),origin)&&("system".equals(value)||"light".equals(value)||"dark".equals(value))){prefs.edit().putString("settingsTheme",value).apply();result.confirm("saved");}else result.cancel();return true;
    }
    if(prompt.equals("codexbridge-language:"+clipboardToken)){
     if(view==web&&GatewayURL.sameOrigin(url,origin)&&GatewayURL.sameOrigin(view.getUrl(),origin)&&("en".equals(value)||"zh".equals(value))){prefs.edit().putString("language",value).apply();menu.setContentDescription(L("电脑与聊天选项"));renderDownload();result.confirm("saved");}else result.cancel();return true;
    }
    if(!prompt.startsWith("codexbridge-copy:"))return false;
    if(view!=web||!view.hasWindowFocus()||!GatewayURL.sameOrigin(url,origin)||!GatewayURL.sameOrigin(view.getUrl(),origin)||!prompt.equals("codexbridge-copy:"+clipboardToken)||value==null||value.length()>262144){result.cancel();return true;}
    try{android.content.ClipboardManager clipboard=(android.content.ClipboardManager)getSystemService(CLIPBOARD_SERVICE);clipboard.setPrimaryClip(ClipData.newPlainText("Codex Bridge",value));result.confirm("copied");}catch(RuntimeException e){result.cancel();}
    return true;
   }
   public boolean onShowFileChooser(WebView view,ValueCallback<Uri[]> callback,FileChooserParams params){
    if(files!=null)files.onReceiveValue(null);files=callback;fileView=view;fileGeneration=generation;
    Intent picker=new Intent(Intent.ACTION_OPEN_DOCUMENT);picker.addCategory(Intent.CATEGORY_OPENABLE);picker.setType("*/*");picker.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);picker.putExtra(Intent.EXTRA_ALLOW_MULTIPLE,params.getMode()==FileChooserParams.MODE_OPEN_MULTIPLE);
    ArrayList<String> types=new ArrayList<>();for(String type:params.getAcceptTypes())if(type!=null&&type.contains("/"))types.add(type);
    if(types.size()==1)picker.setType(types.get(0));else if(!types.isEmpty())picker.putExtra(Intent.EXTRA_MIME_TYPES,types.toArray(new String[0]));
    try{startActivityForResult(picker,31);}catch(Exception e){files.onReceiveValue(null);files=null;fileView=null;message(L("没有可用的文件选择器"));}return true;
   }});

  web.setDownloadListener((target,agent,disposition,mime,length)->downloadArtifact(target));
  watchConnection(url,web,generation);loadWithRecovery(base,url,web,generation);monitor();
 }catch(Exception e){message(e.getMessage());}}
 void renderDownload(){downloadCard.render(downloadTask,savingDownload||choosingDownload!=null,openingDownload,updateSession!=null);}
 void downloadArtifact(String target){
  if(preparingUpdate!=null){message(L("电脑正在准备更新，请稍候"));return;}
  if(downloadTask!=null){downloadCard.showTask();renderDownload();message(L("已有下载任务，请先完成或取消"));return;}
  if(web==null||!GatewayURL.sameOrigin(web.getUrl(),origin)||!ArtifactDownload.accepts(target,origin)){message(L("此下载不是当前电脑的附件，请在浏览器中打开"));return;}
  startDownload(target,origin);
 }
 void startDownload(String target,String base){
  if(restoringUpdate){message(L("正在恢复更新，请稍候"));return;}
  try{downloadTask=new ArtifactDownload.Task(target,base,getCacheDir());downloadCard.showTask();renderDownload();runDownload(downloadTask);}catch(java.io.IOException e){message(e.getMessage());}
 }
 void runDownload(ArtifactDownload.Task task){
  final UpdateInstallSession session=updateSession;task.state=ArtifactDownload.State.READY;renderDownload();
  downloads.execute(()->task.run(()->CookieManager.getInstance().getCookie(task.url),value->{
   // Snapshot the event before posting: queued progress callbacks must not become completion events.
   boolean complete=value.state==ArtifactDownload.State.COMPLETE;
   if(complete&&session!=null)try{session.save();}catch(java.io.IOException e){complete=false;if(value.state!=ArtifactDownload.State.CANCELLED){value.state=ArtifactDownload.State.FAILED;value.restartRequired=true;value.error="无法保存待安装更新，请重新下载";}}
   final boolean finished=complete;
   runOnUiThread(()->{if(downloadTask!=value||isFinishing()||isDestroyed())return;renderDownload();if(finished&&session!=null&&updateSession==session&&session.completed()){autoInstallUpdate=true;resumeUpdateInstall();}
   });
  }));
 }
 void downloadAction(){
  ArtifactDownload.Task task=downloadTask;if(task==null||savingDownload||choosingDownload!=null||openingDownload)return;
  if(task.state==ArtifactDownload.State.RUNNING){task.pause();renderDownload();}
  else if(task.state==ArtifactDownload.State.FAILED&&updateSession!=null){chooseUpdateDownload(updateSession.release,null);}
  else if(task.state==ArtifactDownload.State.PAUSED||task.state==ArtifactDownload.State.FAILED){
   if(task.resumable()){runDownload(task);}else{MobileRelease release=updateSession==null?null:updateSession.release;String target=task.url,base=task.origin;boolean assisted=task.gatewayUpdate;cancelDownload();if(release!=null){if(assisted)startGatewayUpdate(release,base);else startUpdateDownload(release);}else startDownload(target,base);}
  }else if(task.state==ArtifactDownload.State.COMPLETE){
   Intent save=new Intent(Intent.ACTION_CREATE_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("application/octet-stream").putExtra(Intent.EXTRA_TITLE,task.name);
   try{choosingUpdateFile=updateSession==null?null:task.result().file.getName();choosingDownload=task;renderDownload();startActivityForResult(save,32);}catch(Exception e){choosingDownload=null;choosingUpdateFile=null;renderDownload();message(L("没有可用的文件保存器"));}
  }
 }
 void openDownload(){
  if(updateSession!=null){if(downloadTask!=null&&downloadTask.state==ArtifactDownload.State.FAILED)openUpdateUrl(updateSession.release.download);else installDownloadedUpdate();return;}
  ArtifactDownload.Task task=downloadTask;if(task==null||task.state!=ArtifactDownload.State.COMPLETE||savingDownload||choosingDownload!=null||openingDownload)return;
  openingDownload=true;renderDownload();downloads.execute(()->{
   Uri uri=null;try{uri=DownloadProvider.prepare(this,task.result());}catch(java.io.IOException ignored){}
   final Uri target=uri;runOnUiThread(()->{
    openingDownload=false;if(isFinishing()||isDestroyed())return;renderDownload();if(task!=downloadTask)return;
    if(target==null){message(L("打开失败，请重试或保存文件"));return;}
    Intent view=new Intent(Intent.ACTION_VIEW).setDataAndType(target,getContentResolver().getType(target)).addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
    view.setClipData(ClipData.newRawUri("",target));
    try{startActivity(view);}catch(ActivityNotFoundException e){message(L("暂无可打开此文件的应用"));}catch(Exception e){message(L("打开失败，请重试或保存文件"));}
   });
  });
 }
 void cancelDownload(){if(preparingUpdate!=null){preparingUpdate.cancel();preparingUpdate=null;}if(updateSession!=null)updateSession.cancel();else if(downloadTask!=null)downloadTask.cancel();downloadTask=null;updateSession=null;autoInstallUpdate=false;renderDownload();}
 void saveDownload(int result,Intent data){
  if(restoringUpdate&&choosingUpdateFile!=null){pendingSaveResult=new Bundle();pendingSaveResult.putInt("result",result);pendingSaveResult.putParcelable("data",data);return;}
  ArtifactDownload.Task task=choosingDownload;choosingDownload=null;choosingUpdateFile=null;
  if(result!=RESULT_OK||data==null||data.getData()==null){renderDownload();return;}
  if(task==null){renderDownload();message(L("保存失败，请重试"));return;}
  Uri target=data.getData();savingDownload=true;renderDownload();downloads.execute(()->{
   boolean ok=false;
   try{if(task==null||task.state!=ArtifactDownload.State.COMPLETE)throw new java.io.IOException();ArtifactDownload.Result download=task.result();
    try(java.io.InputStream in=new java.io.FileInputStream(download.file);java.io.OutputStream out=getContentResolver().openOutputStream(target,"w")){
     if(out==null)throw new java.io.IOException();byte[] buffer=new byte[16384];int count;while((count=in.read(buffer))!=-1){if(task.state!=ArtifactDownload.State.COMPLETE)throw new java.io.IOException();out.write(buffer,0,count);}
    }if(task.state!=ArtifactDownload.State.COMPLETE)throw new java.io.IOException();ok=true;
   }catch(Exception e){try{android.provider.DocumentsContract.deleteDocument(getContentResolver(),target);}catch(Exception ignored){}}
   final boolean saved=ok;runOnUiThread(()->{savingDownload=false;if(isFinishing()||isDestroyed())return;if(task==downloadTask){if(saved)cancelDownload();else renderDownload();Toast.makeText(this,L(saved?"文件已保存":"保存失败，请重试"),Toast.LENGTH_LONG).show();}else renderDownload();});
  });
 }
 boolean openIntent(Intent intent){final int request=++navigationRequest;try{String base=intent.getStringExtra("origin"),thread=intent.getStringExtra("thread"),host=intent.getStringExtra("host");Uri link=intent.getData();if(link!=null){if(!"codexbridge".equals(link.getScheme())||!"open".equals(link.getHost()))return false;base=link.getQueryParameter("origin");thread=link.getQueryParameter("thread");host=link.getQueryParameter("host");}if(base==null)return false;if(!saved().contains(base)){message(L("请先扫码保存通知对应的电脑，再打开聊天。"));return false;}String target=thread==null||thread.isEmpty()?base+"/":GatewayURL.chat(base,thread,host==null?"local":host);if(thread==null||thread.isEmpty()||!reuseChat(target,base,request))connect(target);return true;}catch(Exception e){message(L("通知链接无效"));return false;}}
 boolean reuseChat(String target,String base,int request){
  if(web==null||!base.equals(origin)||!GatewayURL.isChatPage(web.getUrl(),base))return false;
  final WebView current=web;final int ticket=generation;
  final String binding=EventClient.sessionCookie(CookieManager.getInstance().getCookie(base+"/"),base);
  if(binding.isEmpty())return false;
  String hash="#"+Uri.parse(target).getEncodedFragment();
  current.evaluateJavascript("(()=>typeof window.BridgeOpenChatLink==='function'&&window.BridgeOpenChatLink("+JSONObject.quote(hash)+"))()",accepted->{
   if(web!=current||ticket!=generation||request!=navigationRequest||!base.equals(origin)||!saved().contains(base)||!GatewayURL.isChatPage(current.getUrl(),base)||!binding.equals(EventClient.sessionCookie(CookieManager.getInstance().getCookie(base+"/"),base)))return;
   if(!"true".equals(accepted))connect(target);
  });
  return true;
 }
 @Override protected void onNewIntent(Intent intent){super.onNewIntent(intent);setIntent(intent);openIntent(intent);}
 Dialog panel(String title,LinearLayout content){
  resetExitGesture();
  Dialog dialog=new Dialog(ui.context);dialog.requestWindowFeature(Window.FEATURE_NO_TITLE);
  LinearLayout body=column();body.setBackground(background(0xfff5f5f3,22));body.setPadding(dp(18),dp(12),dp(18),dp(12));
  LinearLayout heading=new LinearLayout(ui.context);heading.setGravity(Gravity.CENTER_VERTICAL);TextView name=text(title,23);name.setTypeface(null,android.graphics.Typeface.BOLD);heading.addView(name,new LinearLayout.LayoutParams(0,-2,1));Button done=button(L("完成"),dialog::dismiss);heading.addView(done);body.addView(heading);
  ScrollView scroll=new ScrollView(ui.context);content.setPadding(0,dp(20),0,dp(16));scroll.addView(content);body.addView(scroll,new LinearLayout.LayoutParams(-1,0,1));
  dialog.setContentView(body);dialog.show();dialog.getWindow().setBackgroundDrawableResource(android.R.color.transparent);dialog.getWindow().setLayout(getResources().getDisplayMetrics().widthPixels-dp(24),getResources().getDisplayMetrics().heightPixels-dp(100));return dialog;
 }
 void clearInbox(String base,JSONObject data,Runnable done){
  final int ticket=generation;final String binding=EventClient.sessionCookie(CookieManager.getInstance().getCookie(base+"/"),base);worker.execute(()->{try{
   JSONObject auth=EventClient.request(base,"/api/auth",null,null);
   if(!binding.equals(EventClient.sessionCookie(CookieManager.getInstance().getCookie(base+"/"),base)))return;
   JSONObject snapshot=EventClient.request(base,"/api/mobile/events/read",new JSONObject().put("provider","all").put("streamId",data.getString("streamId")).put("through",data.getLong("cursor")),auth.getString("csrf"));
   runOnUiThread(()->{if(ticket!=generation||!binding.equals(EventClient.sessionCookie(CookieManager.getInstance().getCookie(base+"/"),base)))return;ComputerNotifications.update(base,binding,snapshot);done.run();});
  }catch(Exception error){runOnUiThread(()->{if(ticket==generation)message(error.getMessage());});}});
 }
 void inbox(){
  if(origin.isEmpty()){message(L("请先连接并登录电脑"));return;}final String base=origin;final int ticket=generation;status.setVisibility(View.VISIBLE);status.setText(L("正在读取通知…"));
  worker.execute(()->{try{JSONObject data=EventClient.read(base);runOnUiThread(()->{
   if(ticket!=generation)return;status.setVisibility(View.GONE);if(!data.optBoolean("enabled")){message(L("请在电脑 Preview 的手机通知中开启“手机 App 通知收件箱”并保存。"));return;}
   LinearLayout content=column();Dialog dialog=panel(L("通知"),content);TextView heading=text(L("最近任务提醒"),22);heading.setTypeface(null,android.graphics.Typeface.BOLD);add(heading,content,16);
   String clearKey="cleared:"+base+":"+data.optString("streamId");long cleared=prefs.getLong(clearKey,0);JSONArray events=new JSONArray();JSONArray all=data.optJSONArray("events");if(all!=null)for(int n=0;n<all.length();n++){JSONObject e=all.optJSONObject(n);if(e!=null&&e.optLong("sequence")>cleared)events.put(e);}
   if(events.length()>0)add(button(L("清空通知"),()->clearInbox(base,data,()->{prefs.edit().putLong(clearKey,data.optLong("cursor")).apply();renderComputerNotifications();android.app.NotificationManager nm=(android.app.NotificationManager)getSystemService(NOTIFICATION_SERVICE);Set<String> tags=new HashSet<>();if(all!=null)for(int n=0;n<all.length();n++){JSONObject e=all.optJSONObject(n);if(e!=null&&e.optLong("sequence")<=data.optLong("cursor"))tags.add(e.optString("id"));}for(android.service.notification.StatusBarNotification note:nm.getActiveNotifications()){Bundle extra=note.getNotification().extras;String prefix="bridge|"+base+"|";boolean remote=false;if(note.getTag()!=null&&note.getTag().startsWith(prefix)){try{remote=Long.parseLong(note.getTag().substring(prefix.length()))<=data.optLong("cursor");}catch(NumberFormatException ignored){}}if(remote||tags.contains(note.getTag())||(base.equals(extra.getString("bridgeOrigin"))&&extra.getLong("bridgeSequence",0)<=data.optLong("cursor")))nm.cancel(note.getTag(),note.getId());}dialog.dismiss();inbox();})),content,12);
   if(events.length()==0){LinearLayout empty=card();add(text(L("暂无通知"),18),empty,8);add(text(L("在聊天中开启“提醒”，任务完成或需要你处理时，会记录在这里。"),14),empty,0);add(empty,content,12);return;}
   for(int i=events.length()-1;i>=0;i--){JSONObject event=events.optJSONObject(i);if(event==null)continue;LinearLayout item=card();TextView title=text(event.optString("title",L("任务提醒")),17);title.setTypeface(null,android.graphics.Typeface.BOLD);add(title,item,8);
    if(!event.optString("body").isEmpty())add(text(event.optString("body"),14),item,8);
    if(event.has("createdAt")){TextView time=text(java.text.DateFormat.getDateTimeInstance(java.text.DateFormat.SHORT,java.text.DateFormat.SHORT).format(new Date((long)(event.optDouble("createdAt")*1000))),12);time.setTextColor(mutedColor());add(time,item,8);}
    add(button(L("查看聊天  ↗"),()->{try{String target=GatewayURL.chat(base,event.getString("threadId"),event.getString("host"));dialog.dismiss();connect(target);}catch(Exception e){message(L("聊天链接无效"));}}),item,0);add(item,content,12);
   }
  });}catch(Exception e){runOnUiThread(()->{if(ticket==generation){status.setVisibility(View.GONE);message(e.getMessage());}});}});
 }
 void settings(){if(settingsPage==null||!settingsPage.isShowing()){settingsPage=new SettingsPage(this,0);settingsPage.show();}}
 void reopenSettings(SettingsPage previous){if(previous!=settingsPage||!previous.isShowing()||previous.isClosing())return;SettingsPage replacement=previous.replacement();previous.dismiss();settingsPage=replacement;settingsPage.show();}
 String appVersion(){try{return getPackageManager().getPackageInfo(getPackageName(),0).versionName;}catch(Exception e){return "2.0.0";}}
 void openUpdateUrl(String url){try{startActivity(new Intent(Intent.ACTION_VIEW,Uri.parse(url)));}catch(ActivityNotFoundException e){message(L("没有可用的浏览器，请在电脑上打开 GitHub Release。"));}}
 void checkUpdate(Dialog dialog,View check,TextView updateStatus){
  check.setEnabled(false);updateStatus.setText(L("正在检查更新…"));final String current=appVersion(),gateway=selectedUpdateGateway();final int ticket=generation;
  worker.execute(()->{
   MobileRelease candidate=null;String problem=null;
   java.net.HttpURLConnection connection=null;
   try{
    connection=(java.net.HttpURLConnection)new java.net.URL(MobileRelease.API).openConnection();connection.setConnectTimeout(15000);connection.setReadTimeout(15000);connection.setInstanceFollowRedirects(false);connection.setRequestProperty("Accept","application/vnd.github+json");connection.setRequestProperty("User-Agent","Codex-Mobile-Bridge/"+current);
    if(connection.getResponseCode()!=200)throw new java.io.IOException();
    java.io.ByteArrayOutputStream bytes=new java.io.ByteArrayOutputStream();
    try(java.io.InputStream in=connection.getInputStream()){byte[] block=new byte[8192];int n;while((n=in.read(block))!=-1){if(bytes.size()+n>4*1024*1024)throw new java.io.IOException();bytes.write(block,0,n);}}
    JSONArray rows=new JSONArray(bytes.toString("UTF-8"));
    for(int i=0;i<rows.length();i++){JSONObject row=rows.getJSONObject(i);JSONArray assets=row.optJSONArray("assets");if(assets==null)continue;
     for(int j=0;j<assets.length();j++){JSONObject asset=assets.getJSONObject(j);String tag=row.optString("tag_name");if(!MobileRelease.eligible(current,tag,row.optBoolean("draft",true),row.optBoolean("prerelease",true),asset.optString("name"),asset.optString("browser_download_url")))continue;
      String version=tag.substring(1);if(candidate==null||MobileRelease.compare(version,candidate.version)>0){String notes=row.optString("body");String digest=asset.optString("digest",null);candidate=new MobileRelease(version,notes.substring(0,Math.min(notes.length(),12000)),asset.optLong("size",-1),digest);}
     }
    }
   }catch(Exception e){problem=L("检查失败，请检查网络后重试。也可以打开版本页面。");}finally{if(connection!=null)connection.disconnect();}
   if(problem!=null&&!gateway.isEmpty()){
    runOnUiThread(()->{if(dialog.isShowing())updateStatus.setText(L("正在通过电脑检查更新…"));});
    try{candidate=new GatewayUpdate(gateway,()->CookieManager.getInstance().getCookie(gateway+"/")).check(current);problem=null;}catch(Exception e){problem=L("手机无法连接 GitHub，电脑协助更新也未成功。")+"\n"+L(e.getMessage()==null?"请检查网络后重试":e.getMessage());}
   }
   final MobileRelease result=candidate;final String error=problem;
   runOnUiThread(()->{if(isFinishing()||isDestroyed()||!dialog.isShowing())return;check.setEnabled(true);if(ticket!=generation){updateStatus.setText(L("电脑连接已变化，请重新检查更新"));return;}
    if(error!=null){updateStatus.setText(error);new AlertDialog.Builder(ui.context).setMessage(error).setNegativeButton(L("关闭"),null).setPositiveButton(L("打开 GitHub 版本页"),(d,w)->openUpdateUrl(MobileRelease.REPO+"/releases")).show();return;}
    if(result==null){updateStatus.setText(L("当前已是此通道最新版本。"));return;}
    final String skipKey="skip-mobile-update:"+MobileRelease.channel(current),title=L(result.version.equals(prefs.getString(skipKey,""))?"已跳过此版本，仍可手动更新":"发现新版本")+" "+result.version;
    updateStatus.setText(title);
    new AlertDialog.Builder(ui.context).setTitle(title).setMessage(L("App 会在本机下载并打开系统安装器，已保存的电脑与登录状态会保留。")+"\n\n"+result.notes).setNegativeButton(L("跳过此版本"),(d,w)->{prefs.edit().putString(skipKey,result.version).apply();updateStatus.setText(L("已跳过此版本，仍可手动更新")+" "+result.version);}).setNeutralButton(L("打开 GitHub 版本页"),(d,w)->openUpdateUrl(result.page())).setPositiveButton(L("下载并安装"),(d,w)->chooseUpdateDownload(result,dialog)).show();
   });
  });
 }
 java.io.File updateDirectory(){return new java.io.File(getFilesDir(),"pending-update");}
 void updateProblem(String text,MobileRelease release){new AlertDialog.Builder(ui.context).setMessage(L(text)).setNegativeButton(L("关闭"),null).setPositiveButton(L("打开 GitHub 版本页"),(d,w)->openUpdateUrl(release.page())).show();}
 void chooseUpdateDownload(MobileRelease release,Dialog settingsDialog){
  Dialog dialog=new Dialog(ui.context);dialog.requestWindowFeature(Window.FEATURE_NO_TITLE);
  LinearLayout content=column();content.setPadding(dp(22),dp(22),dp(22),dp(16));content.setBackground(background(surfaceColor(),26));
  TextView title=text(L("选择下载方式"),22);title.setTypeface(null,android.graphics.Typeface.BOLD);title.setPadding(0,0,0,0);add(title,content,22);
  add(updateDownloadOption("ic_phone","手机下载","从 GitHub 直接下载到手机",()->{if(startUpdateDownload(release)){dialog.dismiss();if(settingsDialog!=null)settingsDialog.dismiss();}}),content,10);
  add(updateDownloadOption("ic_computer","电脑下载","电脑从 GitHub 下载，再传到手机",()->{if(startGatewayUpdate(release,selectedUpdateGateway())){dialog.dismiss();if(settingsDialog!=null)settingsDialog.dismiss();}}),content,14);
  Button cancel=button(L("取消"),dialog::dismiss);cancel.setTextColor(0xff727782);cancel.setBackground(new android.graphics.drawable.RippleDrawable(android.content.res.ColorStateList.valueOf(0x10202124),background(surfaceColor(),12),null));add(cancel,content,0);
  ScrollView scroll=new ScrollView(ui.context);scroll.setFillViewport(true);scroll.addView(content);dialog.setContentView(scroll);dialog.setCanceledOnTouchOutside(true);dialog.show();
  Window window=dialog.getWindow();window.setBackgroundDrawableResource(android.R.color.transparent);window.setDimAmount(.34f);window.setLayout(Math.min(dp(420),getResources().getDisplayMetrics().widthPixels-dp(32)),WindowManager.LayoutParams.WRAP_CONTENT);
 }
 LinearLayout updateDownloadOption(String drawable,String title,String detail,Runnable action){
  LinearLayout row=new LinearLayout(ui.context);row.setGravity(Gravity.CENTER_VERTICAL);row.setPadding(dp(14),dp(17),dp(12),dp(17));row.setMinimumHeight(dp(88));row.setBackground(new android.graphics.drawable.RippleDrawable(android.content.res.ColorStateList.valueOf(0x14202124),background(0xfff5f6f8,17),null));
  ImageView image=new ImageView(ui.context);image.setImageResource(getResources().getIdentifier(drawable,"drawable",getPackageName()));image.setScaleType(ImageView.ScaleType.FIT_CENTER);image.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);row.addView(image,new LinearLayout.LayoutParams(dp(30),dp(30)));
  LinearLayout labels=column();LinearLayout.LayoutParams labelParams=new LinearLayout.LayoutParams(0,-2,1);labelParams.setMarginStart(dp(13));labelParams.setMarginEnd(dp(8));row.addView(labels,labelParams);
  TextView heading=text(L(title),16);heading.setTypeface(null,android.graphics.Typeface.BOLD);heading.setPadding(0,0,0,dp(5));heading.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);labels.addView(heading);
  TextView description=text(L(detail),13);description.setTextColor(0xff747983);description.setPadding(0,0,0,0);description.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);labels.addView(description);
  TextView arrow=text("›",24);arrow.setTextColor(0xffa3a7ae);arrow.setPadding(0,0,0,0);arrow.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);row.addView(arrow);
  row.setContentDescription(L(title)+"，"+L(detail));row.setFocusable(true);row.setOnClickListener(v->action.run());return row;
 }
 String selectedUpdateGateway(){return !origin.isEmpty()&&saved().contains(origin)?origin:"";}
 boolean startUpdateDownload(MobileRelease release){
  if(restoringUpdate){message(L("正在恢复更新，请稍候"));return false;}
  if(preparingUpdate!=null||(downloadTask!=null&&(updateSession==null||downloadTask.state!=ArtifactDownload.State.FAILED))){message(L("已有下载任务，请先完成或取消"));return false;}
  if(downloadTask!=null){
   if(!downloadTask.gatewayUpdate&&updateSession.release==release&&downloadTask.resumable()){runDownload(downloadTask);return true;}
   cancelDownload();
  }
  try{java.io.File directory=updateDirectory();if(!directory.isDirectory()&&!directory.mkdirs())throw new java.io.IOException("无法保存待安装更新，请重新下载");
   downloadTask=new ArtifactDownload.Task(release.download,MobileRelease.REPO,directory,release.size,release.sha256,MobileRelease.asset(release.version));updateSession=new UpdateInstallSession(release,downloadTask,directory);downloadCard.showTask();renderDownload();runDownload(downloadTask);return true;
  }catch(java.io.IOException e){updateSession=null;downloadTask=null;updateProblem(e.getMessage(),release);return false;}
 }
 boolean startGatewayUpdate(MobileRelease release,String gateway){
  if(restoringUpdate||preparingUpdate!=null||(downloadTask!=null&&(updateSession==null||downloadTask.state!=ArtifactDownload.State.FAILED))){message(L("已有下载任务，请先完成或取消"));return false;}
  if(gateway.isEmpty()||!gateway.equals(selectedUpdateGateway())){updateProblem("请先连接并登录电脑网关",release);return false;}
  if(downloadTask!=null&&downloadTask.gatewayUpdate&&updateSession.release==release&&gateway.equals(downloadTask.origin)&&downloadTask.resumable()){runDownload(downloadTask);return true;}
  final GatewayUpdate helper;try{helper=new GatewayUpdate(gateway,()->CookieManager.getInstance().getCookie(gateway+"/"));}catch(java.io.IOException e){updateProblem(e.getMessage(),release);return false;}
  preparingUpdate=helper;final int ticket=generation;LinearLayout content=column();content.setPadding(dp(24),dp(12),dp(24),dp(12));
  TextView detail=text(L("电脑正在下载更新…"),15);add(detail,content,12);ProgressBar progress=new ProgressBar(this,null,android.R.attr.progressBarStyleHorizontal);progress.setMax(1000);progress.setIndeterminate(true);add(progress,content,8);
  AlertDialog dialog=new AlertDialog.Builder(ui.context).setTitle(L("通过电脑更新")+" · "+release.version).setView(content).setNegativeButton(L("取消"),(d,w)->{helper.cancel();if(preparingUpdate==helper)preparingUpdate=null;}).create();dialog.setCanceledOnTouchOutside(false);dialog.setOnCancelListener(d->{helper.cancel();if(preparingUpdate==helper)preparingUpdate=null;});dialog.show();
  downloads.execute(()->{
   ArtifactDownload.Task ready=null;String error=null;
   try{String identifier=helper.prepare(release,(downloaded,size)->runOnUiThread(()->{if(preparingUpdate!=helper||isDestroyed())return;progress.setIndeterminate(false);progress.setProgress((int)(downloaded*1000/size));detail.setText(L("电脑正在下载更新…")+" "+(downloaded*100/size)+"%");}));
    java.io.File directory=updateDirectory();if(!directory.isDirectory()&&!directory.mkdirs())throw new java.io.IOException("无法保存待安装更新，请重新下载");ready=ArtifactDownload.Task.gatewayUpdate(gateway,identifier,release,directory);
   }catch(Exception e){error=e.getMessage()==null?"电脑协助更新失败，请重试":e.getMessage();}
   final ArtifactDownload.Task task=ready;final String problem=error;
   runOnUiThread(()->{
    if(isDestroyed()||isFinishing()||preparingUpdate!=helper||ticket!=generation||!gateway.equals(selectedUpdateGateway())){if(task!=null)task.cancel();if(!isDestroyed())dialog.dismiss();return;}
    preparingUpdate=null;dialog.dismiss();
    if(problem!=null){updateProblem(problem,release);return;}
    if(updateSession!=null)updateSession.cancel();else if(downloadTask!=null)downloadTask.cancel();
    downloadTask=task;updateSession=new UpdateInstallSession(release,task,updateDirectory());downloadCard.showTask();renderDownload();runDownload(task);
   });
  });return true;
 }
 void restoreUpdate(){
  restoringUpdate=true;final String current=appVersion();downloads.execute(()->{
   UpdateInstallSession saved=null;boolean invalid=false;try{saved=UpdateInstallSession.restore(updateDirectory(),current);}catch(java.io.InterruptedIOException interrupted){return;}catch(java.io.IOException e){if(Thread.currentThread().isInterrupted())return;invalid=true;UpdateInstallSession.discard(updateDirectory());}
   final UpdateInstallSession recovered=saved;final boolean failed=invalid;runOnUiThread(()->{
    if(isFinishing()||isDestroyed())return;restoringUpdate=false;
    if(recovered!=null){updateSession=recovered;downloadTask=recovered.task;try{if(downloadTask.result().file.getName().equals(choosingUpdateFile))choosingDownload=downloadTask;}catch(java.io.IOException ignored){}downloadCard.showTask();renderDownload();}
    else if(failed)message(L("待安装更新已失效，请重新检查更新"));
    if(pendingSaveResult!=null){Bundle result=pendingSaveResult;pendingSaveResult=null;saveDownload(result.getInt("result"),result.getParcelable("data"));}
    resumeUpdateInstall();
   });
  });
 }
 void resumeUpdateInstall(){
  if(!resumed||updateSession==null||savingDownload||choosingDownload!=null)return;
  if(updateSession.waitingPermission()){
   try{updateSession.permissionReturned();}catch(java.io.IOException e){updateProblem("无法保存待安装更新，请重新下载",updateSession.release);return;}
   if(Build.VERSION.SDK_INT>=26&&!getPackageManager().canRequestPackageInstalls()){updateProblem("请在系统设置中允许此应用安装未知来源应用。",updateSession.release);return;}
   installDownloadedUpdate();
  }else if(autoInstallUpdate){autoInstallUpdate=false;installDownloadedUpdate();}
 }
 void installDownloadedUpdate(){
  final ArtifactDownload.Task task=downloadTask;final UpdateInstallSession session=updateSession;if(!resumed||task==null||session==null||task.state!=ArtifactDownload.State.COMPLETE||openingDownload||savingDownload||choosingDownload!=null||!session.beginInstall())return;
  if(Build.VERSION.SDK_INT>=26&&!getPackageManager().canRequestPackageInstalls()){try{session.awaitPermission();startActivity(new Intent(android.provider.Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,Uri.parse("package:"+getPackageName())));}catch(Exception e){try{session.permissionReturned();}catch(java.io.IOException ignored){}session.finishInstall();updateProblem("无法打开安装权限设置，请在系统设置中允许此应用安装未知来源应用。",session.release);}return;}
  openingDownload=true;renderDownload();downloads.execute(()->{
   Uri uri=null;try{task.verifyCompletedUpdate();uri=DownloadProvider.prepare(this,task.result());}catch(java.io.IOException ignored){}
   final Uri target=uri;runOnUiThread(()->{openingDownload=false;session.finishInstall();if(isFinishing()||isDestroyed()||task!=downloadTask)return;renderDownload();if(target==null){updateProblem("安装包准备失败，请重新下载",session.release);return;}if(!resumed)return;Intent install=new Intent(Intent.ACTION_VIEW).setDataAndType(target,"application/vnd.android.package-archive").addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);install.setClipData(ClipData.newRawUri("",target));try{startActivity(install);}catch(ActivityNotFoundException e){updateProblem("系统没有可用的安装器，请保存 APK 后手动安装",session.release);}catch(Exception e){updateProblem("打开安装器失败，请重试",session.release);}});
  });
 }
 void enableNativePush(){
  if(Build.VERSION.SDK_INT>=33&&checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)!=PackageManager.PERMISSION_GRANTED){requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS},24);return;}
  try{Class<?> provider=Class.forName("io.github.try2love.codexbridge.FirebaseBootstrap");prefs.edit().putBoolean("nativePushEnabled",true).apply();provider.getMethod("enable",MainActivity.class).invoke(null,this);}catch(Exception e){prefs.edit().putBoolean("nativePushEnabled",false).apply();message(L("此预览尚未配置系统推送项目。当前仅同步收件箱；配置 Firebase 或厂商推送后才能启用后台任务提醒。"));}
 }
 void registerPushToken(){String token=prefs.getString("fcmToken","");if(!token.isEmpty()&&prefs.getBoolean("nativePushEnabled",false))worker.execute(()->{try{EventClient.register(this,token);}catch(Exception ignored){/* Retry after authenticated page load or resume. */}});}
 void toggleBackgroundNotifications(){
  if(prefs.getBoolean("backgroundNotifications",false)){prefs.edit().putBoolean("backgroundNotifications",false).apply();MonitorService.refresh();return;}
  if(Build.VERSION.SDK_INT>=33&&checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)!=PackageManager.PERMISSION_GRANTED){requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS},26);return;}
  if(!((NotificationManager)getSystemService(NOTIFICATION_SERVICE)).areNotificationsEnabled()){message(L("请在系统设置中允许通知。"));return;}
  prefs.edit().putBoolean("alerts",true).putBoolean("backgroundNotifications",true).apply();monitor();
  if(!MonitorService.startBackground(this))message(L("后台通知启动失败，请重新打开 App 后再试。"));
 }
 void requestStartupNotifications(){
  TaskNotifications.createChannel(this,"foreground-tasks");
  // A stored false is an explicit choice (including a denied startup request).
  if(prefs.contains("alerts"))return;
  prefs.edit().putBoolean("alerts",false).apply();
  if(Build.VERSION.SDK_INT>=33&&checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)!=PackageManager.PERMISSION_GRANTED){requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS},25);return;}
  prefs.edit().putBoolean("alerts",((NotificationManager)getSystemService(NOTIFICATION_SERVICE)).areNotificationsEnabled()).apply();
 }
 void toggleNotifications(){if(prefs.getBoolean("alerts",false)){prefs.edit().putBoolean("alerts",false).putBoolean("backgroundNotifications",false).apply();MonitorService.refresh();return;}if(Build.VERSION.SDK_INT>=33&&checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)!=PackageManager.PERMISSION_GRANTED){requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS},22);return;}if(!((NotificationManager)getSystemService(NOTIFICATION_SERVICE)).areNotificationsEnabled()){message(L("请在系统设置中允许通知。"));return;}prefs.edit().putBoolean("alerts",true).apply();monitor();}
 void testNotification(){if(Build.VERSION.SDK_INT>=33&&checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)!=PackageManager.PERMISSION_GRANTED){requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS},23);return;}String base=origin;new Handler().postDelayed(()->{try{TaskNotifications.show(this,base,new JSONObject().put("id","test").put("title",L("手机通知测试")).put("body",L("本机通知已开启")));}catch(Exception ignored){}},10000);message(L("10 秒后显示本地测试通知，可以先返回桌面。此测试不代表远程推送已经接通。"));}
 void confirmRemove(){confirmRemove(origin);}
 void confirmRemove(String target){new AlertDialog.Builder(ui.context).setTitle(L("移除这台电脑？")).setMessage(target+"\n\n"+L("清除本机保存的连接和登录状态，电脑上的聊天不受影响。")).setNegativeButton(L("取消"),null).setPositiveButton(L("移除"),(d,w)->{if(downloadTask!=null&&downloadTask.origin.equals(target))cancelDownload();final int ticket=generation;worker.execute(()->{try{JSONObject auth=EventClient.request(target,"/api/auth",null,null);if(auth.optBoolean("authenticated"))EventClient.request(target,"/api/logout",new JSONObject(),auth.getString("csrf"));}catch(Exception ignored){/* Offline removal is local; server session expiry still applies. */}DeviceBinding.clear(this,target);runOnUiThread(()->{Set<String> values=saved();values.remove(target);EventClient.forget(target);android.content.SharedPreferences.Editor edit=prefs.edit().putStringSet("origins",values).remove("notificationCheckpoint:"+target).remove("cursor:"+target).remove("stream:"+target).remove("connection:"+target).remove("name:"+target).remove("last-opened:"+target).remove("cookieName:"+target).remove("notificationsMuted:"+target).remove("notificationSourceRevision:"+target);if(target.equals(prefs.getString("active","")))edit.remove("active");edit.apply();MonitorService.bindingsChanged();if(target.equals(origin))origin="";if(ticket==generation)home();});});}).show();}
 void monitor(){if(monitor==null){monitor=new ServiceConnection(){public void onServiceConnected(ComponentName n,IBinder b){}public void onServiceDisconnected(ComponentName n){}};getApplicationContext().bindService(new Intent(this,MonitorService.class),monitor,BIND_AUTO_CREATE);}}
 void scan(){if(checkSelfPermission(Manifest.permission.CAMERA)!=PackageManager.PERMISSION_GRANTED){requestPermissions(new String[]{Manifest.permission.CAMERA},21);return;}startActivityForResult(new Intent(this,ScanActivity.class),20);}
 @Override public void onRequestPermissionsResult(int code,String[] names,int[] results){super.onRequestPermissionsResult(code,names,results);boolean allowed=results.length>0&&results[0]==PackageManager.PERMISSION_GRANTED;if(code==25){prefs.edit().putBoolean("alerts",allowed).apply();if(allowed)monitor();return;}if(!allowed){message(L(code==21?"未获得权限，可继续粘贴地址使用。":"请在系统设置中允许通知。"));return;}if(code==26)toggleBackgroundNotifications();if(code==24)enableNativePush();if(code==21)scan();if(code==23)testNotification();if(code==22){prefs.edit().putBoolean("alerts",true).apply();monitor();}}
 void selectedFiles(int result,Intent data){
  ValueCallback<Uri[]> callback=files;WebView target=fileView;int ticket=fileGeneration;files=null;fileView=null;
  if(callback==null)return;
  if(result!=RESULT_OK||target!=web||ticket!=generation){callback.onReceiveValue(null);return;}
  LinkedHashSet<Uri> selected=new LinkedHashSet<>();
  if(data!=null){ClipData clips=data.getClipData();if(clips!=null)for(int i=0;i<clips.getItemCount();i++){Uri uri=clips.getItemAt(i).getUri();if(uri!=null)selected.add(uri);}if(data.getData()!=null)selected.add(data.getData());}
  if(selected.isEmpty()){callback.onReceiveValue(null);message(L("未收到所选文件，请重新选择。"));return;}
  worker.execute(()->{
   boolean readable=true;
   try{for(Uri uri:selected){if(!"content".equals(uri.getScheme()))throw new java.io.IOException();android.content.pm.ProviderInfo provider=getPackageManager().resolveContentProvider(uri.getAuthority(),0);if(provider==null||provider.applicationInfo.uid==android.os.Process.myUid())throw new java.io.IOException();try(android.content.res.AssetFileDescriptor descriptor=getContentResolver().openAssetFileDescriptor(uri,"r")){if(descriptor==null)throw new java.io.IOException();}}}catch(Exception e){readable=false;}
   final boolean accepted=readable;
   runOnUiThread(()->{if(target!=web||ticket!=generation){callback.onReceiveValue(null);return;}callback.onReceiveValue(accepted?selected.toArray(new Uri[0]):null);if(!accepted)message(L("无法读取所选文件，请从系统文件选择器重新选择。"));});
  });
 }
 @Override protected void onActivityResult(int code,int result,Intent data){super.onActivityResult(code,result,data);if(code==32){saveDownload(result,data);return;}if(code==20&&result==RESULT_OK&&data!=null)choose(data.getStringExtra("code"));if(code==31)selectedFiles(result,data);}
 @Override protected void onSaveInstanceState(Bundle state){state.putString("choosingUpdateFile",choosingUpdateFile);if(pendingSaveResult!=null)state.putBundle("pendingSaveResult",pendingSaveResult);if(settingsPage!=null&&settingsPage.isShowing()&&!settingsPage.isClosing()){state.putBoolean("settingsOpen",true);state.putInt("settingsScroll",settingsPage.scrollPosition());}super.onSaveInstanceState(state);}
 @Override protected void onResume(){super.onResume();resumed=true;if(ui.dark!=new SettingsUi(SettingsUi.themed(this)).dark)applyTheme();ComputerNotifications.onChange=()->runOnUiThread(this::renderComputerNotifications);checkComputers();MonitorService.setVisible(true);if(!MonitorService.startBackground(this))message(L("后台通知启动失败，请重新打开 App 后再试。"));if(web!=null)web.onResume();registerPushToken();resumeUpdateInstall();if(settingsPage!=null&&settingsPage.isShowing())settingsPage.refresh();}
 // Keep the live WebView and its drafts/tabs when rotating or resizing the window.
 @Override public void onConfigurationChanged(android.content.res.Configuration config){super.onConfigurationChanged(config);dismissGatewayMenu();if("system".equals(SettingsUi.theme(this))&&ui.dark!=new SettingsUi(SettingsUi.themed(this)).dark)applyTheme();screen.requestApplyInsets();downloadCard.post(downloadCard::place);if(web!=null){web.requestLayout();web.invalidate();}if(settingsPage!=null&&"system".equals(SettingsUi.theme(this)))reopenSettings(settingsPage);}
 @Override protected void onPause(){dismissGatewayMenu();if(routeDiscovery!=null){routeDiscovery.cancel();routeDiscovery=null;}routeAttempted=false;ComputerNotifications.onChange=null;resumed=false;stopComputerChecks();resetExitGesture();MonitorService.setVisible(false);if(web!=null)web.onPause();CookieManager.getInstance().flush();super.onPause();}
 void resetExitGesture(){homeBackAt=0;if(exitToast!=null){exitToast.cancel();exitToast=null;}}
 @Override public void onBackPressed(){handleBack();}
 void handleBack(){if(gatewayPopup!=null){dismissGatewayMenu();return;}if(web==null){long now=SystemClock.elapsedRealtime();if(homeBackAt!=0&&now-homeBackAt<=2000){resetExitGesture();finishAndRemoveTask();}else{homeBackAt=now;if(exitToast!=null)exitToast.cancel();exitToast=Toast.makeText(this,L("再按一次返回退出 App"),Toast.LENGTH_SHORT);exitToast.show();}return;}resetExitGesture();if(!GatewayURL.sameOrigin(web.getUrl(),origin)){home();return;}final int ticket=generation;web.evaluateJavascript("(()=>{const dialog=document.querySelector('dialog[open]');if(dialog){if(dialog.dispatchEvent(new Event('cancel',{cancelable:true})))dialog.close();return true;}if(typeof window.BridgeNavigation?.back==='function')return window.BridgeNavigation.back();if(window.BridgeWorkbench?.back())return true;if(document.getElementById('app')?.classList.contains('chat-open')){document.getElementById('back')?.click();return true;}return false;})()",result->{if(ticket==generation&&!"true".equals(result))home();});}
 @Override public void onDestroy(){prefs.unregisterOnSharedPreferenceChangeListener(this);if(settingsPage!=null)settingsPage.dismiss();if(downloadTask!=null&&(updateSession==null||downloadTask.state!=ArtifactDownload.State.COMPLETE))downloadTask.cancel();downloads.shutdownNow();clear();probes.shutdownNow();worker.shutdownNow();if(monitor!=null)getApplicationContext().unbindService(monitor);super.onDestroy();}
}
