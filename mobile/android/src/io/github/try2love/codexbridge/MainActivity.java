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

public final class MainActivity extends Activity {
 private LinearLayout root,bar;private WebView web;private String origin="";private TextView status;
 private android.content.SharedPreferences prefs;private final ExecutorService worker=Executors.newSingleThreadExecutor();
 private final ExecutorService probes=Executors.newFixedThreadPool(3);
 private final Handler homeHandler=new Handler(Looper.getMainLooper());
 private final Map<String,TextView> homeStates=new LinkedHashMap<>();
 private final List<GatewayProbe> homeProbes=new ArrayList<>();
 private final List<Future<?>> probeTasks=new ArrayList<>();
 private int probeGeneration;private boolean resumed;
 private final Runnable homeRefresh=()->checkComputers();
 private long homeBackAt;private Toast exitToast;
 private boolean downloading;private ArtifactDownload.Result pendingDownload;
 private ServiceConnection monitor;private ValueCallback<Uri[]> files;private WebView fileView;private int fileGeneration;private int generation;
 String L(String value){return MobileStrings.text(this,value);}
 int dp(int n){return (int)(getResources().getDisplayMetrics().density*n);}
 TextView text(String value,int size){TextView t=new TextView(this);t.setText(value);t.setTextSize(size);t.setTextColor(0xff202124);t.setPadding(0,dp(5),0,dp(5));return t;}
 android.graphics.drawable.GradientDrawable background(int color,int radius){android.graphics.drawable.GradientDrawable d=new android.graphics.drawable.GradientDrawable();d.setColor(color);d.setCornerRadius(dp(radius));return d;}
 Button button(String value,Runnable action){Button b=new Button(this);b.setText(value);b.setTextSize(16);b.setTextColor(0xff202124);b.setAllCaps(false);b.setMinHeight(dp(54));b.setStateListAnimator(null);b.setElevation(0);b.setTranslationZ(0);b.setPadding(dp(18),dp(10),dp(18),dp(10));android.graphics.drawable.GradientDrawable face=background(0xfff4f5f7,12);b.setBackground(new android.graphics.drawable.RippleDrawable(android.content.res.ColorStateList.valueOf(0x18202124),face,null));b.setOnClickListener(v->action.run());return b;}
 LinearLayout column(){LinearLayout c=new LinearLayout(this);c.setOrientation(1);return c;}
 void add(View v,LinearLayout parent,int bottom){LinearLayout.LayoutParams p=new LinearLayout.LayoutParams(-1,-2);p.bottomMargin=dp(bottom);parent.addView(v,p);}
 LinearLayout card(){LinearLayout c=column();c.setPadding(dp(20),dp(16),dp(20),dp(16));c.setBackground(background(0xffffffff,22));return c;}
 ImageButton icon(String name,String description,Runnable action){ImageButton b=new ImageButton(this);b.setImageResource(getResources().getIdentifier(name,"drawable",getPackageName()));b.setContentDescription(description);b.setPadding(dp(12),dp(12),dp(12),dp(12));android.util.TypedValue ripple=new android.util.TypedValue();getTheme().resolveAttribute(android.R.attr.selectableItemBackgroundBorderless,ripple,true);b.setBackgroundResource(ripple.resourceId);b.setOnClickListener(v->action.run());return b;}
 void header(boolean chat){bar.setVisibility(chat?View.GONE:View.VISIBLE);if(chat)return;bar.removeAllViews();ImageView logo=new ImageView(this);logo.setImageResource(getResources().getIdentifier("ic_bridge","drawable",getPackageName()));LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(dp(30),dp(30));lp.setMarginEnd(dp(8));bar.addView(logo,lp);TextView title=text("Codex Bridge",18);title.setTypeface(null,android.graphics.Typeface.BOLD);bar.addView(title,new LinearLayout.LayoutParams(0,-2,1));bar.addView(icon("ic_bell",L("通知收件箱"),this::inbox),new LinearLayout.LayoutParams(dp(48),dp(48)));bar.addView(icon("ic_settings",L("手机设置"),this::settings),new LinearLayout.LayoutParams(dp(48),dp(48)));}
 void webAction(String selector){if(web!=null&&GatewayURL.sameOrigin(web.getUrl(),origin))web.evaluateJavascript("document.querySelector("+JSONObject.quote(selector)+")?.click()",null);}
 void gatewayMenu(View anchor){PopupMenu menu=new PopupMenu(this,anchor);menu.getMenu().add(L("返回电脑列表")).setOnMenuItemClickListener(i->{home();return true;});menu.getMenu().add(L("账号与接入")).setOnMenuItemClickListener(i->{webAction("#accounts-button");return true;});menu.getMenu().add(L("通知收件箱")).setOnMenuItemClickListener(i->{inbox();return true;});menu.getMenu().add(L("外观与显示")).setOnMenuItemClickListener(i->{webAction("[data-open-appearance]");return true;});menu.getMenu().add(L("手机设置")).setOnMenuItemClickListener(i->{settings();return true;});menu.getMenu().add(L("刷新页面")).setOnMenuItemClickListener(i->{if(web!=null)web.reload();return true;});for(int n=0;n<menu.getMenu().size();n++){int[] icons={android.R.drawable.ic_menu_revert,android.R.drawable.ic_menu_myplaces,android.R.drawable.ic_dialog_email,android.R.drawable.ic_menu_edit,android.R.drawable.ic_menu_preferences,android.R.drawable.ic_popup_sync};menu.getMenu().getItem(n).setIcon(icons[n]);}if(Build.VERSION.SDK_INT>=29)menu.setForceShowIcon(true);menu.show();}
 String webAppearance(){try(java.io.ByteArrayOutputStream out=new java.io.ByteArrayOutputStream()){for(String asset:new String[]{"mobile-ui.js","mobile-clipboard.js","mobile-session.js"}){try(java.io.InputStream in=getAssets().open(asset)){byte[] data=new byte[4096];int size;while((size=in.read(data))!=-1)out.write(data,0,size);}}return out.toString("UTF-8");}catch(java.io.IOException e){throw new IllegalStateException("Missing mobile layout",e);}}
 void message(String value){resetExitGesture();new AlertDialog.Builder(this).setMessage(L(value)).setPositiveButton(L("好"),null).show();}
 Set<String> saved(){return new LinkedHashSet<>(prefs.getStringSet("origins",new HashSet<>()));}
 @Override public void onCreate(Bundle state){super.onCreate(state);prefs=getSharedPreferences("bridge",0);root=new LinearLayout(this);root.setOrientation(1);root.setBackgroundColor(0xfff5f5f3);root.setFitsSystemWindows(Build.VERSION.SDK_INT<30);setContentView(root);
  if(Build.VERSION.SDK_INT>=33)getOnBackInvokedDispatcher().registerOnBackInvokedCallback(android.window.OnBackInvokedDispatcher.PRIORITY_DEFAULT,this::handleBack);
  if(Build.VERSION.SDK_INT>=30){getWindow().setDecorFitsSystemWindows(false);root.setOnApplyWindowInsetsListener((v,insets)->{android.graphics.Insets i=insets.getInsets(WindowInsets.Type.systemBars()|WindowInsets.Type.displayCutout()|WindowInsets.Type.ime());v.setPadding(i.left,i.top,i.right,i.bottom);return WindowInsets.CONSUMED;});}
  bar=new LinearLayout(this);bar.setGravity(Gravity.CENTER_VERTICAL);bar.setPadding(dp(20),dp(4),dp(12),dp(4));root.addView(bar);header(false);
  status=text("",12);status.setTextColor(0xff6b7075);status.setGravity(Gravity.CENTER);root.addView(status);
  if(!openIntent(getIntent())){String active=prefs.getString("active","");if(saved().contains(active))connect(active+"/");else home();}monitor();
 }
 void clear(){if(files!=null){files.onReceiveValue(null);files=null;fileView=null;}stopComputerChecks();homeStates.clear();resetExitGesture();generation++;if(web!=null){web.stopLoading();web.destroy();web=null;}while(root.getChildCount()>2)root.removeViewAt(2);}
 void home(){clear();header(false);status.setVisibility(View.GONE);ScrollView scroll=new ScrollView(this);scroll.setFillViewport(true);LinearLayout content=column();content.setPadding(dp(20),dp(16),dp(20),dp(24));scroll.addView(content);root.addView(scroll,new LinearLayout.LayoutParams(-1,0,1));
  ImageView icon=new ImageView(this);icon.setImageResource(getResources().getIdentifier("ic_computer","drawable",getPackageName()));icon.setScaleType(ImageView.ScaleType.FIT_CENTER);LinearLayout.LayoutParams ip=new LinearLayout.LayoutParams(dp(64),dp(58));ip.gravity=Gravity.CENTER;ip.bottomMargin=dp(24);content.addView(icon,ip);
  TextView headline=text(L("电脑上的工作，\n带在身边。"),30);headline.setTypeface(null,android.graphics.Typeface.BOLD);headline.setGravity(Gravity.CENTER);add(headline,content,10);
  TextView intro=text(L("继续聊天、查看结果，让电脑替你运行。"),15);intro.setTextColor(0xff777b80);intro.setGravity(Gravity.CENTER);add(intro,content,26);
  LinearLayout connection=card();Button scan=button(L("扫码连接电脑"),this::scan);scan.setTextColor(0xffffffff);scan.setBackground(background(0xff202124,16));add(scan,connection,8);add(button(L("输入网关地址"),this::manualAddress),connection,0);add(connection,content,24);
  TextView section=text(L("你的电脑"),19);section.setTypeface(null,android.graphics.Typeface.BOLD);add(section,content,12);
  if(saved().isEmpty()){LinearLayout empty=card();add(text(L("还没有连接的电脑"),17),empty,6);TextView hint=text(L("在电脑网关中展开“扫码登录”，然后用上方按钮扫描。"),14);hint.setTextColor(0xff777b80);add(hint,empty,0);add(empty,content,18);}
  for(String entry:saved()){
   LinearLayout computer=card(),heading=new LinearLayout(this);heading.setGravity(Gravity.CENTER_VERTICAL);
   TextView reachability=text(L("检查中…"),12);reachability.setTextColor(0xff777b80);reachability.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);heading.addView(reachability);homeStates.put(entry,reachability);
   TextView name=text(computerName(entry),17);name.setTypeface(null,android.graphics.Typeface.BOLD);name.setSingleLine();name.setEllipsize(android.text.TextUtils.TruncateAt.END);name.setPadding(dp(10),dp(5),dp(8),dp(5));heading.addView(name,new LinearLayout.LayoutParams(0,-2,1));heading.addView(text("›",22));add(heading,computer,4);
   TextView address=text(entry,13);address.setTextColor(0xff777b80);add(address,computer,4);
   LinearLayout actions=new LinearLayout(this);Button rename=button(L("重命名"),()->renameComputer(entry));Button remove=button(L("移除这台电脑"),()->confirmRemove(entry));remove.setTextColor(0xffb44235);
   LinearLayout.LayoutParams renameSize=new LinearLayout.LayoutParams(0,-2,1);renameSize.setMarginEnd(dp(8));actions.addView(rename,renameSize);actions.addView(remove,new LinearLayout.LayoutParams(0,-2,1));add(actions,computer,0);
   computer.setOnClickListener(v->connect(entry+"/"));computer.setContentDescription(L("连接 ")+computerName(entry));add(computer,content,12);
  }
  TextView foot=text(L("外出使用 HTTPS 地址；局域网地址需要连接同一网络。"),13);foot.setTextColor(0xff777b80);foot.setGravity(Gravity.CENTER);add(foot,content,0);checkComputers();
 }
 String computerName(String address){return prefs.getString("name:"+address,Uri.parse(address).getHost());}
 void renameComputer(String address){
  EditText field=new EditText(this);field.setSingleLine();field.setFilters(new android.text.InputFilter[]{new android.text.InputFilter.LengthFilter(80)});field.setText(computerName(address));field.selectAll();field.setPadding(dp(24),dp(16),dp(24),dp(16));
  new AlertDialog.Builder(this).setTitle(L("重命名电脑")).setMessage(address+"\n"+L("留空恢复默认名称")).setView(field).setNegativeButton(L("取消"),null).setPositiveButton(L("保存"),(d,w)->{String name=field.getText().toString().trim();android.content.SharedPreferences.Editor edit=prefs.edit();if(name.isEmpty())edit.remove("name:"+address);else edit.putString("name:"+address,name);edit.apply();home();}).show();
 }
 void stopComputerChecks(){probeGeneration++;homeHandler.removeCallbacks(homeRefresh);for(GatewayProbe probe:homeProbes)probe.cancel();homeProbes.clear();for(Future<?> task:probeTasks)task.cancel(true);probeTasks.clear();}
 void checkComputers(){
  stopComputerChecks();if(!resumed||web!=null||homeStates.isEmpty())return;final int ticket=probeGeneration;
  for(Map.Entry<String,TextView> entry:homeStates.entrySet()){
   GatewayProbe probe=new GatewayProbe();homeProbes.add(probe);
   probeTasks.add(probes.submit(()->{boolean online=probe.check(entry.getKey());runOnUiThread(()->{if(ticket!=probeGeneration||!resumed||web!=null)return;TextView label=entry.getValue();label.setText((online?"● ":"○ ")+L(online?"在线":"暂不可达"));label.setTextColor(online?0xff23815b:0xff777b80);});}));
  }
  homeHandler.postDelayed(homeRefresh,30000);
 }
 void manualAddress(){resetExitGesture();EditText field=new EditText(this);field.setSingleLine();field.setInputType(17);field.setText("https://");field.setSelection(field.length());field.setPadding(dp(24),dp(16),dp(24),dp(16));new AlertDialog.Builder(this).setTitle(L("连接电脑")).setMessage(L("粘贴电脑网关提供的访问地址")).setView(field).setPositiveButton(L("继续"),(d,w)->choose(field.getText().toString())).setNegativeButton(L("取消"),null).show();}
 void choose(String value){resetExitGesture();try{String target=GatewayURL.connection(value);String base=GatewayURL.origin(value);new AlertDialog.Builder(this).setTitle(L("连接到这台电脑？")).setMessage(base+L("\n请确认这是你自己的网关。")).setPositiveButton(L("连接"),(d,w)->connect(target)).setNegativeButton(L("取消"),null).show();}catch(Exception e){message(e.getMessage());}}
 void connect(String url){try{String base=GatewayURL.origin(url);clear();header(true);status.setVisibility(View.VISIBLE);origin=base;Set<String> saved=saved();saved.add(origin);prefs.edit().putStringSet("origins",saved).putString("active",origin).apply();status.setText(L("正在连接 · ")+origin);
  FrameLayout viewport=new FrameLayout(this);root.addView(viewport,new LinearLayout.LayoutParams(-1,0,1));web=new WebView(this);web.setBackgroundColor(0xfff7f9fc);viewport.addView(web,new FrameLayout.LayoutParams(-1,-1));ImageButton menu=icon("ic_more",L("电脑与聊天选项"),()->{});menu.setOnClickListener(v->gatewayMenu(v));FrameLayout.LayoutParams menuPosition=new FrameLayout.LayoutParams(dp(48),dp(48),Gravity.TOP|Gravity.END);menuPosition.topMargin=dp(4);menuPosition.setMarginEnd(dp(8));viewport.addView(menu,menuPosition);final String clipboardToken=UUID.randomUUID().toString();final String appearance=webAppearance().replace("__BRIDGE_CLIPBOARD_TOKEN__",clipboardToken);WebSettings s=web.getSettings();s.setJavaScriptEnabled(true);s.setDomStorageEnabled(true);s.setAllowFileAccess(false);s.setAllowContentAccess(true);s.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);s.setSupportMultipleWindows(false);s.setUserAgentString(s.getUserAgentString()+" BridgeMobile/0.1-Android");CookieManager.getInstance().setAcceptThirdPartyCookies(web,false);
  web.setWebViewClient(new WebViewClient(){
   public boolean shouldOverrideUrlLoading(WebView view,WebResourceRequest request){String target=request.getUrl().toString();if(target.equals("codexbridge://home")&&request.isForMainFrame()&&request.hasGesture()&&GatewayURL.sameOrigin(view.getUrl(),origin)){home();return true;}if(ArtifactDownload.accepts(target,origin)&&request.isForMainFrame()&&request.hasGesture()){downloadArtifact(target);return true;}if(GatewayURL.sameOrigin(target,origin))return false;if(request.isForMainFrame()&&request.hasGesture()&&("https".equals(request.getUrl().getScheme())||"http".equals(request.getUrl().getScheme())))new AlertDialog.Builder(MainActivity.this).setMessage(L("在浏览器打开外部链接？\n")+request.getUrl().getHost()).setPositiveButton(L("打开"),(d,w)->startActivity(new Intent(Intent.ACTION_VIEW,request.getUrl()))).setNegativeButton(L("取消"),null).show();return true;}
   public void onPageFinished(WebView view,String target){if(GatewayURL.sameOrigin(target,origin)){CookieManager.getInstance().flush();view.evaluateJavascript(appearance,null);status.setVisibility(View.GONE);registerPushToken();}}
   public void onReceivedError(WebView view,WebResourceRequest req,WebResourceError e){if(req.isForMainFrame()){status.setVisibility(View.VISIBLE);status.setText(L("连接失败，请在更多菜单中刷新"));}}
  });
  web.setWebChromeClient(new WebChromeClient(){
   @Override public boolean onJsPrompt(WebView view,String url,String prompt,String value,JsPromptResult result){
    if(prompt.equals("codexbridge-computer:"+clipboardToken)){
     if(view==web&&GatewayURL.sameOrigin(url,origin)&&GatewayURL.sameOrigin(view.getUrl(),origin))result.confirm(computerName(origin));else result.cancel();return true;
    }
    if(prompt.equals("codexbridge-session:"+clipboardToken)){
     if(view==web&&GatewayURL.sameOrigin(url,origin)&&GatewayURL.sameOrigin(view.getUrl(),origin)){CookieManager.getInstance().flush();result.confirm("saved");}else result.cancel();return true;
    }
    if(prompt.equals("codexbridge-language:"+clipboardToken)){
     if(view==web&&GatewayURL.sameOrigin(url,origin)&&GatewayURL.sameOrigin(view.getUrl(),origin)&&("en".equals(value)||"zh".equals(value))){prefs.edit().putString("language",value).apply();menu.setContentDescription(L("电脑与聊天选项"));result.confirm("saved");}else result.cancel();return true;
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
  web.loadUrl(url);monitor();
 }catch(Exception e){message(e.getMessage());}}
 void downloadArtifact(String target){
  if(downloading||pendingDownload!=null){message(L("已有下载进行中，请稍候"));return;}
  if(web==null||!GatewayURL.sameOrigin(web.getUrl(),origin)||!ArtifactDownload.accepts(target,origin)){message(L("此下载不是当前电脑的附件，请在浏览器中打开"));return;}
  final String base=origin,cookie=CookieManager.getInstance().getCookie(target);final int ticket=generation;
  downloading=true;status.setText(L("正在下载…"));status.setVisibility(View.VISIBLE);
  worker.execute(()->{try{ArtifactDownload.Result result=ArtifactDownload.fetch(target,base,cookie,getCacheDir());runOnUiThread(()->{downloading=false;status.setVisibility(View.GONE);if(ticket!=generation||isFinishing()||isDestroyed()){result.file.delete();return;}pendingDownload=result;Intent save=new Intent(Intent.ACTION_CREATE_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("application/octet-stream").putExtra(Intent.EXTRA_TITLE,result.name);try{startActivityForResult(save,32);}catch(Exception e){pendingDownload=null;result.file.delete();message(L("没有可用的文件保存器"));}});}catch(Exception e){runOnUiThread(()->{downloading=false;status.setVisibility(View.GONE);if(ticket==generation&&!isFinishing()&&!isDestroyed())message(e instanceof java.io.IOException?e.getMessage():L("下载失败，请重试"));});}});
 }
 void saveDownload(int result,Intent data){
  ArtifactDownload.Result download=pendingDownload;pendingDownload=null;if(download==null)return;
  if(result!=RESULT_OK||data==null||data.getData()==null){download.file.delete();return;}
  Uri target=data.getData();worker.execute(()->{boolean ok=false;try(java.io.InputStream in=new java.io.FileInputStream(download.file);java.io.OutputStream out=getContentResolver().openOutputStream(target,"w")){if(out==null)throw new java.io.IOException();byte[] buffer=new byte[16384];int count;while((count=in.read(buffer))!=-1)out.write(buffer,0,count);ok=true;}catch(Exception e){try{android.provider.DocumentsContract.deleteDocument(getContentResolver(),target);}catch(Exception ignored){}}finally{download.file.delete();}final boolean saved=ok;runOnUiThread(()->{if(!isFinishing()&&!isDestroyed())message(saved?L("文件已保存"):L("保存失败，请重试"));});});
 }
 boolean openIntent(Intent intent){try{String base=intent.getStringExtra("origin"),thread=intent.getStringExtra("thread"),host=intent.getStringExtra("host");Uri link=intent.getData();if(link!=null){if(!"codexbridge".equals(link.getScheme())||!"open".equals(link.getHost()))return false;base=link.getQueryParameter("origin");thread=link.getQueryParameter("thread");host=link.getQueryParameter("host");}if(base==null)return false;if(!saved().contains(base)){message(L("请先扫码保存通知对应的电脑，再打开聊天。"));return false;}connect(thread==null||thread.isEmpty()?base+"/":GatewayURL.chat(base,thread,host==null?"local":host));return true;}catch(Exception e){message(L("通知链接无效"));return false;}}
 @Override protected void onNewIntent(Intent intent){super.onNewIntent(intent);setIntent(intent);openIntent(intent);}
 Dialog panel(String title,LinearLayout content){
  resetExitGesture();
  Dialog dialog=new Dialog(this);dialog.requestWindowFeature(Window.FEATURE_NO_TITLE);
  LinearLayout body=column();body.setBackground(background(0xfff5f5f3,22));body.setPadding(dp(18),dp(12),dp(18),dp(12));
  LinearLayout heading=new LinearLayout(this);heading.setGravity(Gravity.CENTER_VERTICAL);TextView name=text(title,23);name.setTypeface(null,android.graphics.Typeface.BOLD);heading.addView(name,new LinearLayout.LayoutParams(0,-2,1));Button done=button(L("完成"),dialog::dismiss);heading.addView(done);body.addView(heading);
  ScrollView scroll=new ScrollView(this);content.setPadding(0,dp(20),0,dp(16));scroll.addView(content);body.addView(scroll,new LinearLayout.LayoutParams(-1,0,1));
  dialog.setContentView(body);dialog.show();dialog.getWindow().setBackgroundDrawableResource(android.R.color.transparent);dialog.getWindow().setLayout(getResources().getDisplayMetrics().widthPixels-dp(24),getResources().getDisplayMetrics().heightPixels-dp(100));return dialog;
 }
 void inbox(){
  if(origin.isEmpty()){message(L("请先连接并登录电脑"));return;}final String base=origin;final int ticket=generation;status.setVisibility(View.VISIBLE);status.setText(L("正在读取通知…"));
  worker.execute(()->{try{JSONObject data=EventClient.read(base);runOnUiThread(()->{
   if(ticket!=generation)return;status.setVisibility(View.GONE);if(!data.optBoolean("enabled")){message(L("请在电脑 Preview 的手机通知中开启“手机 App 通知收件箱”并保存。"));return;}
   LinearLayout content=column();Dialog dialog=panel(L("通知"),content);TextView heading=text(L("最近任务提醒"),22);heading.setTypeface(null,android.graphics.Typeface.BOLD);add(heading,content,16);
   String clearKey="cleared:"+base+":"+data.optString("streamId");long cleared=prefs.getLong(clearKey,0);JSONArray events=new JSONArray();JSONArray all=data.optJSONArray("events");if(all!=null)for(int n=0;n<all.length();n++){JSONObject e=all.optJSONObject(n);if(e!=null&&e.optLong("sequence")>cleared)events.put(e);}
   if(events.length()>0)add(button(L("清空通知"),()->{prefs.edit().putLong(clearKey,data.optLong("cursor")).apply();android.app.NotificationManager nm=(android.app.NotificationManager)getSystemService(NOTIFICATION_SERVICE);Set<String> tags=new HashSet<>();if(all!=null)for(int n=0;n<all.length();n++){JSONObject e=all.optJSONObject(n);if(e!=null&&e.optLong("sequence")<=data.optLong("cursor"))tags.add(e.optString("id"));}for(android.service.notification.StatusBarNotification note:nm.getActiveNotifications()){Bundle extra=note.getNotification().extras;String prefix="bridge|"+base+"|";boolean remote=false;if(note.getTag()!=null&&note.getTag().startsWith(prefix)){try{remote=Long.parseLong(note.getTag().substring(prefix.length()))<=data.optLong("cursor");}catch(NumberFormatException ignored){}}if(remote||tags.contains(note.getTag())||(base.equals(extra.getString("bridgeOrigin"))&&extra.getLong("bridgeSequence",0)<=data.optLong("cursor")))nm.cancel(note.getTag(),note.getId());}dialog.dismiss();inbox();}),content,12);
   if(events.length()==0){LinearLayout empty=card();add(text(L("暂无通知"),18),empty,8);add(text(L("在聊天中开启“提醒”，任务完成或需要你处理时，会记录在这里。"),14),empty,0);add(empty,content,12);return;}
   for(int i=events.length()-1;i>=0;i--){JSONObject event=events.optJSONObject(i);if(event==null)continue;LinearLayout item=card();TextView title=text(event.optString("title",L("任务提醒")),17);title.setTypeface(null,android.graphics.Typeface.BOLD);add(title,item,8);
    if(!event.optString("body").isEmpty())add(text(event.optString("body"),14),item,8);
    if(event.has("createdAt")){TextView time=text(java.text.DateFormat.getDateTimeInstance(java.text.DateFormat.SHORT,java.text.DateFormat.SHORT).format(new Date((long)(event.optDouble("createdAt")*1000))),12);time.setTextColor(0xff777b80);add(time,item,8);}
    add(button(L("查看聊天  ↗"),()->{try{String target=GatewayURL.chat(base,event.getString("threadId"),event.getString("host"));dialog.dismiss();connect(target);}catch(Exception e){message(L("聊天链接无效"));}}),item,0);add(item,content,12);
   }
  });}catch(Exception e){runOnUiThread(()->{if(ticket==generation){status.setVisibility(View.GONE);message(e.getMessage());}});}});
 }
 void settings(){
  LinearLayout content=column();Dialog dialog=panel(L("设置"),content);add(text(L("通知"),19),content,12);
  LinearLayout notifications=card();Button toggle=button(prefs.getBoolean("alerts",false)?L("关闭任务通知"):L("开启任务通知"),()->{dialog.dismiss();toggleNotifications();});add(toggle,notifications,8);add(text(L("App 打开时提醒已连接电脑的新任务消息。离开 App 或锁屏后不保证通知；可在电脑端配置 Bark 或 ntfy。"),14),notifications,12);
  add(button(L("测试本机通知"),()->{dialog.dismiss();testNotification();}),notifications,4);add(text(L("10 秒后显示，用于检查手机的通知权限。"),13),notifications,0);add(notifications,content,22);
  add(text(L("当前电脑"),19),content,12);LinearLayout computer=card();if(origin.isEmpty()){add(text(L("尚未选择电脑"),17),computer,8);add(text(L("返回首页扫码或输入网关地址。"),14),computer,0);}else{add(text(computerName(origin),17),computer,8);add(text(origin,13),computer,12);Button remove=button(L("移除这台电脑"),()->{dialog.dismiss();confirmRemove();});remove.setTextColor(0xffb44235);add(remove,computer,0);}add(computer,content,24);
  LinearLayout updates=card();add(text(L("应用更新"),19),updates,8);add(text("Bridge Preview · "+appVersion(),14),updates,8);add(text(L("预览通道 · 手动检查，不自动安装"),13),updates,8);
  TextView updateStatus=text("",13);Button check=button(L("检查更新"),()->{});check.setOnClickListener(v->checkUpdate(dialog,check,updateStatus));add(check,updates,6);add(updateStatus,updates,0);add(updates,content,20);
  TextView version=text("Codex Mobile Bridge",13);version.setTextColor(0xff777b80);version.setGravity(Gravity.CENTER);add(version,content,0);
 }
 String appVersion(){try{return getPackageManager().getPackageInfo(getPackageName(),0).versionName;}catch(Exception e){return "2.0.0-preview.3";}}
 void openUpdateUrl(String url){try{startActivity(new Intent(Intent.ACTION_VIEW,Uri.parse(url)));}catch(ActivityNotFoundException e){message(L("没有可用的浏览器，请在电脑上打开 GitHub Release。"));}}
 void checkUpdate(Dialog dialog,Button check,TextView updateStatus){
  check.setEnabled(false);updateStatus.setText(L("正在检查更新…"));final String current=appVersion();
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
      String version=tag.substring(1);if(candidate==null||MobileRelease.compare(version,candidate.version)>0){String notes=row.optString("body");candidate=new MobileRelease(version,notes.substring(0,Math.min(notes.length(),12000)));}
     }
    }
   }catch(Exception e){problem=L("检查失败，请检查网络后重试。也可以打开版本页面。");}finally{if(connection!=null)connection.disconnect();}
   final MobileRelease result=candidate;final String error=problem;
   runOnUiThread(()->{if(isFinishing()||isDestroyed()||!dialog.isShowing())return;check.setEnabled(true);
    if(error!=null){updateStatus.setText(error);new AlertDialog.Builder(this).setMessage(error).setNegativeButton(L("关闭"),null).setPositiveButton(L("版本页面"),(d,w)->openUpdateUrl(MobileRelease.REPO+"/releases")).show();return;}
    if(result==null){updateStatus.setText(L("当前已是此通道最新版本。"));return;}
    updateStatus.setText(L("发现新版本")+" · "+result.version);
    new AlertDialog.Builder(this).setTitle(L("发现新版本")+" · "+result.version).setMessage(L("下载后按系统提示覆盖安装，不要先卸载。已保存的电脑与登录状态会保留。")+"\n\n"+result.notes).setNegativeButton(L("稍后"),null).setNeutralButton(L("更新说明"),(d,w)->openUpdateUrl(result.page())).setPositiveButton(L("下载 APK"),(d,w)->openUpdateUrl(result.download)).show();
   });
  });
 }
 void enableNativePush(){
  if(Build.VERSION.SDK_INT>=33&&checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)!=PackageManager.PERMISSION_GRANTED){requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS},24);return;}
  try{Class<?> provider=Class.forName("io.github.try2love.codexbridge.FirebaseBootstrap");prefs.edit().putBoolean("nativePushEnabled",true).apply();provider.getMethod("enable",MainActivity.class).invoke(null,this);}catch(Exception e){prefs.edit().putBoolean("nativePushEnabled",false).apply();message(L("此预览尚未配置系统推送项目。当前仅同步收件箱；配置 Firebase 或厂商推送后才能启用后台任务提醒。"));}
 }
 void registerPushToken(){String token=prefs.getString("fcmToken","");if(!token.isEmpty()&&prefs.getBoolean("nativePushEnabled",false))worker.execute(()->{try{EventClient.register(this,token);}catch(Exception ignored){/* Retry after authenticated page load or resume. */}});}
 void toggleNotifications(){if(Build.VERSION.SDK_INT>=33&&checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)!=PackageManager.PERMISSION_GRANTED){requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS},22);return;}prefs.edit().putBoolean("alerts",!prefs.getBoolean("alerts",false)).apply();monitor();}
 void testNotification(){if(Build.VERSION.SDK_INT>=33&&checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)!=PackageManager.PERMISSION_GRANTED){requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS},23);return;}String base=origin;new Handler().postDelayed(()->{try{MonitorService.notifyEvent(this,base,new JSONObject().put("id","test").put("title",L("手机通知测试")).put("body",L("本机通知已开启")));}catch(Exception ignored){}},10000);message(L("10 秒后显示本地测试通知，可以先返回桌面。此测试不代表远程推送已经接通。"));}
 void confirmRemove(){confirmRemove(origin);}
 void confirmRemove(String target){new AlertDialog.Builder(this).setTitle(L("移除这台电脑？")).setMessage(target+"\n\n"+L("清除本机保存的连接和登录状态，电脑上的聊天不受影响。")).setNegativeButton(L("取消"),null).setPositiveButton(L("移除"),(d,w)->{final int ticket=generation;worker.execute(()->{try{JSONObject auth=EventClient.request(target,"/api/auth",null,null);if(auth.optBoolean("authenticated"))EventClient.request(target,"/api/logout",new JSONObject(),auth.getString("csrf"));}catch(Exception ignored){/* Offline removal is local; server session expiry still applies. */}runOnUiThread(()->{Set<String> values=saved();values.remove(target);CookieManager.getInstance().setCookie(target,"codex_mobile_session=; Path=/; Max-Age=0");CookieManager.getInstance().flush();android.content.SharedPreferences.Editor edit=prefs.edit().putStringSet("origins",values).remove("cursor:"+target).remove("stream:"+target).remove("name:"+target);if(target.equals(prefs.getString("active","")))edit.remove("active");edit.apply();if(target.equals(origin))origin="";if(ticket==generation)home();});});}).show();}
 void monitor(){boolean on=prefs.getBoolean("alerts",false);if(on&&monitor==null){monitor=new ServiceConnection(){public void onServiceConnected(ComponentName n,IBinder b){}public void onServiceDisconnected(ComponentName n){}};getApplicationContext().bindService(new Intent(this,MonitorService.class),monitor,BIND_AUTO_CREATE);}else if(!on&&monitor!=null){getApplicationContext().unbindService(monitor);monitor=null;}}
 void scan(){if(checkSelfPermission(Manifest.permission.CAMERA)!=PackageManager.PERMISSION_GRANTED){requestPermissions(new String[]{Manifest.permission.CAMERA},21);return;}startActivityForResult(new Intent(this,ScanActivity.class),20);}
 @Override public void onRequestPermissionsResult(int code,String[] names,int[] results){super.onRequestPermissionsResult(code,names,results);if(results.length==0||results[0]!=PackageManager.PERMISSION_GRANTED){message(L("未获得权限，可继续粘贴地址使用。"));return;}if(code==24)enableNativePush();if(code==21)scan();if(code==23)testNotification();if(code==22){prefs.edit().putBoolean("alerts",true).apply();monitor();}}
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
 @Override protected void onResume(){super.onResume();resumed=true;checkComputers();MonitorService.foregroundEpoch++;MonitorService.visible=true;if(web!=null)web.onResume();registerPushToken();}
 // Keep the live WebView and its drafts/tabs when rotating or resizing the window.
 @Override public void onConfigurationChanged(android.content.res.Configuration config){super.onConfigurationChanged(config);root.requestApplyInsets();if(web!=null){web.requestLayout();web.invalidate();}}
 @Override protected void onPause(){resumed=false;stopComputerChecks();resetExitGesture();MonitorService.visible=false;if(web!=null)web.onPause();CookieManager.getInstance().flush();super.onPause();}
 void resetExitGesture(){homeBackAt=0;if(exitToast!=null){exitToast.cancel();exitToast=null;}}
 @Override public void onBackPressed(){handleBack();}
 void handleBack(){if(web==null){long now=SystemClock.elapsedRealtime();if(homeBackAt!=0&&now-homeBackAt<=2000){resetExitGesture();finishAndRemoveTask();}else{homeBackAt=now;if(exitToast!=null)exitToast.cancel();exitToast=Toast.makeText(this,L("再按一次返回退出 App"),Toast.LENGTH_SHORT);exitToast.show();}return;}resetExitGesture();if(!GatewayURL.sameOrigin(web.getUrl(),origin)){home();return;}final int ticket=generation;web.evaluateJavascript("(()=>{const dialog=document.querySelector('dialog[open]');if(dialog){dialog.close();return true;}if(window.BridgeWorkbench?.back())return true;if(document.getElementById('app')?.classList.contains('chat-open')){document.getElementById('back')?.click();return true;}return false;})()",result->{if(ticket==generation&&!"true".equals(result))home();});}
 @Override public void onDestroy(){if(pendingDownload!=null){pendingDownload.file.delete();pendingDownload=null;}clear();probes.shutdownNow();worker.shutdownNow();if(monitor!=null)getApplicationContext().unbindService(monitor);super.onDestroy();}
}
