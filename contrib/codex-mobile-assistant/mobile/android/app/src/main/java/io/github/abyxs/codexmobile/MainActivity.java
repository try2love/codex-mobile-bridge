package io.github.abyxs.codexmobile;

import android.Manifest;
import android.app.Activity;
import android.app.DownloadManager;
import android.os.Environment;
import android.webkit.URLUtil;
import android.app.AlertDialog;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.net.Uri;
import android.net.http.SslError;
import android.os.Build;
import android.os.Bundle;
import android.provider.Settings;
import android.view.View;
import android.view.WindowInsets;
import android.view.inputmethod.InputMethodManager;
import android.webkit.CookieManager;
import android.webkit.SslErrorHandler;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebStorage;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.ProgressBar;
import android.widget.RadioGroup;
import android.widget.TextView;
import android.widget.Toast;
import java.net.URI;
import java.util.ArrayList;
import java.util.Locale;
import com.google.zxing.integration.android.IntentIntegrator;
import com.google.zxing.integration.android.IntentResult;

public final class MainActivity extends Activity {
    private static final int PICK_IMAGES = 10;
    private static final int CAMERA_PERMISSION = 21;
    private SharedPreferences preferences;
    private WebView web;
    private EditText address;
    private ProgressBar progress;
    private RadioGroup modes;
    private CheckBox autoConnect;
    private String mode, temporaryDraft, fixedDraft;
    private URI currentSite;
    private ValueCallback<Uri[]> imageCallback;
    private boolean imageSelectionOnly = true;
    private boolean pageFailed;
    private boolean resetHistory;
    private android.webkit.WebMessagePort chromePort;
    private AppDownloads appDownloads;

    @Override public void onCreate(Bundle savedInstanceState) {
        setTheme(R.style.AppTheme);
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);
        View root = findViewById(R.id.root);
        if (Build.VERSION.SDK_INT >= 30) {
            getWindow().setDecorFitsSystemWindows(false);
            root.setOnApplyWindowInsetsListener((view, insets) -> {
                android.graphics.Insets bars = insets.getInsets(
                    WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout());
                int keyboard = insets.getInsets(WindowInsets.Type.ime()).bottom;
                view.setPadding(bars.left, bars.top, bars.right, Math.max(bars.bottom, keyboard));
                return insets;
            });
            root.requestApplyInsets();
        }
        preferences = getSharedPreferences("connection", MODE_PRIVATE);
        appDownloads = new AppDownloads(this, this::publishDownloads);
        temporaryDraft = preferences.getString("temporary", "");
        fixedDraft = preferences.getString("fixed", "");
        mode = preferences.getString("mode", "temporary");
        if (!mode.equals("fixed")) mode = "temporary";
        address = findViewById(R.id.address);
        progress = findViewById(R.id.progress);
        modes = findViewById(R.id.modes);
        autoConnect = findViewById(R.id.auto_connect);
        autoConnect.setChecked(preferences.getBoolean("auto_connect", true));
        autoConnect.setOnCheckedChangeListener((button, checked) ->
            preferences.edit().putBoolean("auto_connect", checked).apply());
        modes.check(mode.equals("fixed") ? R.id.fixed : R.id.temporary);
        updateMode();
        modes.setOnCheckedChangeListener((group, checked) -> {
            if (mode.equals("fixed")) fixedDraft = address.getText().toString();
            else temporaryDraft = address.getText().toString();
            mode = checked == R.id.fixed ? "fixed" : "temporary";
            updateMode();
        });
        findViewById(R.id.connect).setOnClickListener(v -> connect());
        address.setOnEditorActionListener((view, action, event) -> {
            if (action == android.view.inputmethod.EditorInfo.IME_ACTION_GO) {
                connect();
                return true;
            }
            return false;
        });
        findViewById(R.id.paste).setOnClickListener(v -> {
            ClipboardManager clipboard = (ClipboardManager) getSystemService(CLIPBOARD_SERVICE);
            ClipData clip = clipboard.getPrimaryClip();
            if (clip != null && clip.getItemCount() > 0) {
                address.setText(clip.getItemAt(0).coerceToText(this));
                address.setSelection(address.length());
            } else Toast.makeText(this, "剪贴板中没有地址", Toast.LENGTH_SHORT).show();
        });
        findViewById(R.id.menu).setOnClickListener(v -> showMenu());
        findViewById(R.id.scan).setOnClickListener(v -> scanConnection());
        findViewById(R.id.retry).setOnClickListener(v -> reload());
        findViewById(R.id.error_settings).setOnClickListener(v -> showSetup(true));
        findViewById(R.id.resume).setOnClickListener(v -> showSetup(false));
        configureWebView();
        if (Build.VERSION.SDK_INT >= 33) {
            getOnBackInvokedDispatcher().registerOnBackInvokedCallback(
                android.window.OnBackInvokedDispatcher.PRIORITY_DEFAULT, this::handleBack);
        }
        if (autoConnect.isChecked() && !address.getText().toString().trim().isEmpty()) connect();
    }

    private void updateMode() {
        address.setText(mode.equals("fixed") ? fixedDraft : temporaryDraft);
        ((TextView) findViewById(R.id.mode_hint)).setText(mode.equals("fixed")
            ? "使用自己部署的固定域名；电脑端需选择固定域名模式。"
            : "无需服务器和域名，粘贴电脑端生成的 HTTPS 临时链接；链接变化后在此更新。");
        findViewById(R.id.address_error).setVisibility(View.GONE);
    }

    private void scanConnection() {
        if (!getPackageManager().hasSystemFeature(PackageManager.FEATURE_CAMERA_ANY)) {
            new AlertDialog.Builder(this).setMessage("此设备没有可用相机，请粘贴连接地址。")
                .setPositiveButton("好", null).show();
            return;
        }
        if (checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.CAMERA}, CAMERA_PERMISSION);
            return;
        }
        try {
            InputMethodManager keyboard = (InputMethodManager) getSystemService(INPUT_METHOD_SERVICE);
            if (keyboard != null) keyboard.hideSoftInputFromWindow(address.getWindowToken(), 0);
            new IntentIntegrator(this)
                .setCaptureActivity(QrScannerActivity.class)
                .setDesiredBarcodeFormats(IntentIntegrator.QR_CODE)
                .setPrompt("对准电脑助手上的连接二维码\n识别后可确认连接地址；返回键取消")
                .setBeepEnabled(false)
                .setBarcodeImageEnabled(false)
                .setOrientationLocked(false)
                .initiateScan();
        } catch (RuntimeException | LinkageError error) {
            android.util.Log.e("CodexScanner", "Unable to launch scanner", error);
            showScannerError(QrScannerActivity.errorDetails("打开扫码页面", error));
        }
    }

    private void showScannerError(String detail) {
        new AlertDialog.Builder(this).setTitle("暂时无法打开相机")
            .setMessage("请检查相机权限，或先粘贴连接地址。\n\n" + detail)
            .setNegativeButton("关闭", null)
            .setPositiveButton("复制错误信息", (dialog, which) -> {
                ClipboardManager clipboard = (ClipboardManager) getSystemService(CLIPBOARD_SERVICE);
                if (clipboard != null) clipboard.setPrimaryClip(ClipData.newPlainText("扫码错误", detail));
            }).show();
    }

    @Override public void onRequestPermissionsResult(int requestCode, String[] permissions,
                                                     int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == 42) {
            if (grantResults.length > 0 && grantResults[0] == PackageManager.PERMISSION_GRANTED) enableTaskNotifications();
            else Toast.makeText(this,"未允许通知，后台任务提醒未开启",Toast.LENGTH_LONG).show();
            return;
        }
        if (requestCode != CAMERA_PERMISSION) return;
        if (grantResults.length > 0 && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
            scanConnection();
        } else {
            new AlertDialog.Builder(this).setTitle("需要相机权限")
                .setMessage("允许使用相机后即可扫码，也可以继续粘贴连接地址。")
                .setNegativeButton("取消", null)
                .setPositiveButton("打开权限设置", (dialog, which) ->
                    startActivity(new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                        Uri.parse("package:" + getPackageName())))).show();
        }
    }

    private void applyScannedAddress(String value) {
        final URI destination;
        try {
            if (value.length() > 2048 || !value.trim().toLowerCase(Locale.ROOT).startsWith("https://"))
                throw new IllegalArgumentException("Not an HTTPS QR code");
            destination = normalizeAddress(value);
        } catch (Exception error) {
            new AlertDialog.Builder(this).setTitle("无法使用这个二维码")
                .setMessage("请扫描电脑助手生成的 HTTPS 连接二维码，当前地址未更改。")
                .setPositiveButton("好", null).show();
            return;
        }
        new AlertDialog.Builder(this).setTitle("连接到这个入口？")
            .setMessage(destination.toString())
            .setNegativeButton("取消", null)
            .setPositiveButton("连接", (dialog, which) -> {
                address.setText(destination.toString());
                connect();
            }).show();
    }

    private static URI normalizeAddress(String value) throws Exception {
        String text = value.trim();
        if (!text.contains("://")) text = "https://" + text;
        URI uri = new URI(text);
        String path = uri.getRawPath();
        if (!"https".equalsIgnoreCase(uri.getScheme()) || uri.getHost() == null
            || uri.getRawUserInfo() != null || uri.getRawQuery() != null
            || uri.getRawFragment() != null || (path != null && !path.isEmpty() && !path.equals("/"))
            || uri.getPort() == 0 || uri.getPort() > 65535) {
            throw new IllegalArgumentException("Invalid gateway address");
        }
        return new URI("https", null, uri.getHost().toLowerCase(Locale.ROOT),
            uri.getPort(), "/", null, null);
    }

    private void connect() {
        final URI destination;
        try {
            destination = normalizeAddress(address.getText().toString());
        } catch (Exception error) {
            TextView message = findViewById(R.id.address_error);
            message.setText("请填写 HTTPS 入口地址，例如 https://your-domain.example，不含账号、密码或其他路径。");
            message.setVisibility(View.VISIBLE);
            return;
        }
        String normalized = destination.toString();
        preferences.edit().putString(mode, normalized).putString("mode", mode).apply();
        if (mode.equals("fixed")) fixedDraft = normalized;
        else temporaryDraft = normalized;
        address.setText(normalized);
        ((InputMethodManager) getSystemService(INPUT_METHOD_SERVICE))
            .hideSoftInputFromWindow(address.getWindowToken(), 0);
        address.clearFocus();
        // Reopening settings must not discard the current draft or running chat.
        boolean sameSite = currentSite != null && currentSite.equals(destination);
        showSetup(false);
        if (sameSite && !pageFailed) return;
        stopService(new Intent(this,TaskNotificationService.class));
        preferences.edit().putBoolean("task_notifications",false).apply();
        currentSite = destination;
        findViewById(R.id.scan).setVisibility(View.VISIBLE);
        resetHistory = true;
        ((TextView) findViewById(R.id.site_title)).setText("Codex 手机助手");
        findViewById(R.id.menu).setVisibility(View.VISIBLE);
        web.stopLoading();
        web.clearHistory();
        reload();
    }

    private void showSetup(boolean show) {
        findViewById(R.id.setup).setVisibility(show ? View.VISIBLE : View.GONE);
        findViewById(R.id.browser).setVisibility(show ? View.GONE : View.VISIBLE);
        findViewById(R.id.resume).setVisibility(currentSite == null ? View.GONE : View.VISIBLE);
        if (show) findViewById(R.id.address_error).setVisibility(View.GONE);
        if (show) updateHomeConnection("idle");
        else if (web != null && chromePort != null) web.evaluateJavascript("if(typeof notifyNativeChatHeader==='function')notifyNativeChatHeader(!document.getElementById('app').hidden&&document.getElementById('app').classList.contains('chat-open'))", null);
    }

    private void configureWebView() {
        web = findViewById(R.id.web);
        web.setBackgroundColor(Color.WHITE);
        web.setDownloadListener((url, userAgent, disposition, mime, length) -> {
            Uri uri = Uri.parse(url);
            if (!isCurrentOrigin(uri)) {
                Toast.makeText(this, "只能下载当前助手提供的文件", Toast.LENGTH_LONG).show();
                return;
            }
            if (openConversationFile(uri)) return;
            try {
                String name = URLUtil.guessFileName(url, disposition, mime);
                java.util.regex.Matcher encoded = java.util.regex.Pattern.compile("filename\\*=UTF-8''([^;]+)", java.util.regex.Pattern.CASE_INSENSITIVE).matcher(disposition == null ? "" : disposition);
                if (encoded.find()) name = Uri.decode(encoded.group(1));
                name = name.replace('/', '_').replace('\\', '_');
                if (reuseLegacyDownload(uri)) return;
                String cookie = CookieManager.getInstance().getCookie(url);
                String downloadId = appDownloads.start(url, name, cookie, userAgent);
                if (appDownloads.readyFile(downloadId, uri) != null) exportAppDownload(downloadId, uri);
                publishDownloads();
            } catch (Exception error) {
                downloadStatus("无法开始下载，请重试或检查存储空间。", 0);
                Toast.makeText(this, "无法开始下载，请重试或检查存储空间", Toast.LENGTH_LONG).show();
            }
        });
        WebSettings settings = web.getSettings();
        settings.setUserAgentString(settings.getUserAgentString() + " CodexMobileNative/3");
        settings.setJavaScriptEnabled(true);
        settings.setSupportZoom(false);
        settings.setBuiltInZoomControls(false);
        settings.setDisplayZoomControls(false);
        settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(false);
        // System-selected content URIs are needed by the image upload control.
        settings.setAllowContentAccess(true);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setJavaScriptCanOpenWindowsAutomatically(false);
        settings.setMediaPlaybackRequiresUserGesture(true);
        CookieManager.getInstance().setAcceptCookie(true);
        CookieManager.getInstance().setAcceptThirdPartyCookies(web, false);
        web.setWebChromeClient(new WebChromeClient() {
            @Override public void onReceivedTitle(WebView view, String title) {
                if (view.getUrl() != null && isCurrentOrigin(Uri.parse(view.getUrl()))) {
                    ((TextView) findViewById(R.id.site_title)).setText(
                        title == null || title.trim().isEmpty() ? "Codex 手机助手" : title);
                }
            }
            @Override public void onProgressChanged(WebView view, int value) {
                progress.setProgress(value);
                progress.setVisibility(value == 100 || pageFailed ? View.INVISIBLE : View.VISIBLE);
            }
            @Override public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback,
                                                       FileChooserParams parameters) {
                if (imageCallback != null) imageCallback.onReceiveValue(null);
                imageCallback = callback;
                Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT);
                String[] accepted = parameters.getAcceptTypes();
                imageSelectionOnly = accepted != null && accepted.length == 1 && "image/*".equals(accepted[0]);
                intent.setType(imageSelectionOnly ? "image/*" : "*/*");
                intent.addCategory(Intent.CATEGORY_OPENABLE);
                intent.putExtra(Intent.EXTRA_ALLOW_MULTIPLE,
                    parameters.getMode() == FileChooserParams.MODE_OPEN_MULTIPLE);
                try {
                    startActivityForResult(Intent.createChooser(intent, "选择文件"), PICK_IMAGES);
                } catch (android.content.ActivityNotFoundException error) {
                    imageCallback.onReceiveValue(null);
                    imageCallback = null;
                    Toast.makeText(MainActivity.this, "未找到文件选择器", Toast.LENGTH_SHORT).show();
                }
                return true;
            }
        });
        web.setWebViewClient(new WebViewClient() {
            @Override public void onPageStarted(WebView view, String url, android.graphics.Bitmap favicon) {
                closeChromePort();
                updateHomeConnection(isCurrentOrigin(Uri.parse(url)) ? "connecting" : "idle");
            }
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                if (!request.isForMainFrame()) return false;
                if (isCurrentOrigin(request.getUrl()) && "/__assistant_downloads".equals(request.getUrl().getPath())) { handleDownloadCommand(request.getUrl()); return true; }
                if (openConversationFile(request.getUrl())) return true;
                if (isCurrentOrigin(request.getUrl())) return false;
                if (request.hasGesture()) openExternal(request.getUrl());
                else showFailure("入口跳转到了其他地址，请在连接设置中核对最终的 HTTPS 地址。");
                return true;
            }
            @Override public void onPageFinished(WebView view, String url) {
                CookieManager.getInstance().flush();
                if (!pageFailed && isCurrentOrigin(Uri.parse(url))) connectChromePort();
                if (!pageFailed && isCurrentOrigin(Uri.parse(url))) findViewById(R.id.scan).setVisibility(View.GONE);
                progress.setVisibility(View.INVISIBLE);
                // Never retain a previous server in the back stack.
                if (resetHistory && isCurrentOrigin(Uri.parse(url))) {
                    view.clearHistory();
                    resetHistory = false;
                }
            }
            @Override public void onReceivedError(WebView view, WebResourceRequest request,
                                                  WebResourceError error) {
                if (request.isForMainFrame()) showFailure(
                    "请检查手机网络，并确认电脑、Codex 和助手正在运行。临时链接失效时，请更新连接地址。");
            }
            @Override public void onReceivedHttpError(WebView view, WebResourceRequest request,
                                                      WebResourceResponse response) {
                if (request.isForMainFrame()) showFailure("入口返回 HTTP " + response.getStatusCode()
                    + "，请确认助手和服务器连接正常后重试。");
            }
            @Override public void onReceivedSslError(WebView view, SslErrorHandler handler, SslError error) {
                handler.cancel();
                if (isCurrentOrigin(Uri.parse(error.getUrl())))
                    showFailure("HTTPS 证书无法验证，请检查手机时间或服务器证书后重试。");
            }
        });
    }

    private boolean openConversationFile(Uri uri) {
        if (!isCurrentOrigin(uri) || uri.getPath() == null ||
            !uri.getPath().matches("^/api/sessions/[0-9a-f-]{36}/files/[a-f0-9]{64}$")) return false;
        String script = "typeof window.openConversationFile==='function' && window.openConversationFile(" + org.json.JSONObject.quote(uri.toString()) + ")";
        web.evaluateJavascript(script, result -> {
            if (!"true".equals(result)) Toast.makeText(this, "请更新并重新启动电脑端助手，再重新加载页面。文件尚未下载。", Toast.LENGTH_LONG).show();
        });
        return true;
    }

    private void downloadStatus(String text, int percent) { Toast.makeText(this,text,Toast.LENGTH_LONG).show(); publishDownloads(); }
    private org.json.JSONArray storedDownloads() {
        try { return new org.json.JSONArray(preferences.getString("download_records","[]")); }
        catch(Exception error){return new org.json.JSONArray();}
    }
    private void rememberDownload(long id, String name, String sourceURL) throws org.json.JSONException {
        org.json.JSONArray rows=storedDownloads();org.json.JSONObject row=new org.json.JSONObject();
        row.put("id",String.valueOf(id));row.put("name",name);row.put("origin",currentSite.toString());
        row.put("sourceURL",sourceURL);row.put("createdAt",System.currentTimeMillis());rows.put(row);
        preferences.edit().putString("download_records",rows.toString()).apply();
    }
    private void publishDownloads() {
        if(currentSite==null||web==null||web.getUrl()==null||!isCurrentOrigin(Uri.parse(web.getUrl())))return;
        org.json.JSONArray result=new org.json.JSONArray(),stored=storedDownloads();
        DownloadManager manager=(DownloadManager)getSystemService(DOWNLOAD_SERVICE);
        for(int i=0;i<stored.length();i++)try{
            org.json.JSONObject item=stored.getJSONObject(i);if(!currentSite.toString().equals(item.optString("origin")))continue;
            org.json.JSONObject row=new org.json.JSONObject();row.put("id",item.getString("id"));row.put("name",item.getString("name"));
            row.put("sourceURL",item.optString("sourceURL"));row.put("createdAt",item.optLong("createdAt",0));
            if(item.optBoolean("legacyPaused")) {
                row.put("state","paused");row.put("message","旧版系统任务已暂停，需点击重新下载迁移到可续传下载");
                row.put("restartRequired",true);row.put("percent",item.optInt("percent",0));result.put(row);continue;
            }
            row.put("state","failed");row.put("message","记录已移除或下载已取消");row.put("percent",0);
            try(android.database.Cursor cursor=manager.query(new DownloadManager.Query().setFilterById(Long.parseLong(item.getString("id"))))){
                if(cursor!=null&&cursor.moveToFirst()){
                    if(row.optString("sourceURL").isEmpty())row.put("sourceURL",cursor.getString(cursor.getColumnIndexOrThrow(DownloadManager.COLUMN_URI)));
                    int state=cursor.getInt(cursor.getColumnIndexOrThrow(DownloadManager.COLUMN_STATUS));
                    long done=cursor.getLong(cursor.getColumnIndexOrThrow(DownloadManager.COLUMN_BYTES_DOWNLOADED_SO_FAR));
                    long total=cursor.getLong(cursor.getColumnIndexOrThrow(DownloadManager.COLUMN_TOTAL_SIZE_BYTES));
                    row.put("percent",total>0?(int)(done*100/total):-1);
                    if(state==DownloadManager.STATUS_SUCCESSFUL){row.put("state","ready");row.put("percent",100);row.put("message","已下载到 App，点击保存文件选择保存位置");}
                    else if(state==DownloadManager.STATUS_FAILED){row.put("message","下载失败，请检查网络、登录状态和存储空间后重新下载");}
                    else{row.put("state","running");row.put("message",(state==DownloadManager.STATUS_PAUSED?"等待网络 · ":"正在下载 · ")+android.text.format.Formatter.formatFileSize(this,done));}
                }
            }
            result.put(row);
        }catch(Exception ignored){}
        org.json.JSONArray nativeRows=appDownloads.rows(Uri.parse(currentSite.toString()));
        for(int i=0;i<nativeRows.length();i++)result.put(nativeRows.optJSONObject(i));
        web.evaluateJavascript("window.assistantDownloads?.("+result.toString()+")",null);
    }

    private void exportAppDownload(String id, Uri origin) {
        java.io.File file=appDownloads.readyFile(id,origin);
        if(file==null){Toast.makeText(this,"App 内文件已不存在，请重新下载",Toast.LENGTH_LONG).show();return;}
        preferences.edit().putString("pending_download_export",Uri.fromFile(file).toString()).apply();
        startActivityForResult(new Intent(Intent.ACTION_CREATE_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE)
            .setType("application/octet-stream").putExtra(Intent.EXTRA_TITLE,appDownloads.name(id,origin)),704);
    }

    private boolean reuseLegacyDownload(Uri uri) {
        org.json.JSONArray rows=storedDownloads(); DownloadManager manager=(DownloadManager)getSystemService(DOWNLOAD_SERVICE);
        for(int i=rows.length()-1;i>=0;i--)try{
            org.json.JSONObject row=rows.getJSONObject(i);
            if(!row.optString("origin").equals(currentSite.toString())||!row.optString("sourceURL").equals(uri.toString()))continue;
            if(row.optBoolean("legacyPaused")){publishDownloads();return true;}
            long id=Long.parseLong(row.getString("id"));
            try(android.database.Cursor cursor=manager.query(new DownloadManager.Query().setFilterById(id))){
                if(cursor!=null&&cursor.moveToFirst()){
                    int status=cursor.getInt(cursor.getColumnIndexOrThrow(DownloadManager.COLUMN_STATUS));
                    if(status==DownloadManager.STATUS_SUCCESSFUL&&manager.getUriForDownloadedFile(id)!=null){
                        handleDownloadCommand(uri.buildUpon().path("/__assistant_downloads").clearQuery().appendQueryParameter("action","save").appendQueryParameter("id",String.valueOf(id)).build());return true;
                    }
                    if(status!=DownloadManager.STATUS_FAILED){publishDownloads();return true;}
                }
            }
        }catch(Exception ignored){}
        return false;
    }

    private void pauseLegacyDownloads(String onlyId) {
        org.json.JSONArray rows=storedDownloads();DownloadManager manager=(DownloadManager)getSystemService(DOWNLOAD_SERVICE);
        for(int i=0;i<rows.length();i++)try{
            org.json.JSONObject row=rows.getJSONObject(i);String id=row.getString("id");
            if(onlyId!=null&&!onlyId.equals(id))continue;
            try(android.database.Cursor cursor=manager.query(new DownloadManager.Query().setFilterById(Long.parseLong(id)))){
                if(cursor==null||!cursor.moveToFirst())continue;
                int status=cursor.getInt(cursor.getColumnIndexOrThrow(DownloadManager.COLUMN_STATUS));
                if(status==DownloadManager.STATUS_SUCCESSFUL||status==DownloadManager.STATUS_FAILED)continue;
                long total=cursor.getLong(cursor.getColumnIndexOrThrow(DownloadManager.COLUMN_TOTAL_SIZE_BYTES));
                long done=cursor.getLong(cursor.getColumnIndexOrThrow(DownloadManager.COLUMN_BYTES_DOWNLOADED_SO_FAR));
                row.put("percent",total>0?done*100/total:-1);row.put("legacyPaused",true);manager.remove(Long.parseLong(id));
            }
        }catch(Exception ignored){}
        preferences.edit().putString("download_records",rows.toString()).apply();publishDownloads();
    }
    private void handleDownloadCommand(Uri uri) {
        String action=uri.getQueryParameter("action"),id=uri.getQueryParameter("id");
        if("list".equals(action)){publishDownloads();return;}
        if(id!=null&&id.startsWith("app-")) {
            if("pause".equals(action)||"cancel".equals(action))appDownloads.pause(id,uri);
            if("delete".equals(action))appDownloads.delete(id,uri);
            if("save".equals(action))exportAppDownload(id,uri);
            if("resume".equals(action)||"restart".equals(action)) {
                String source=appDownloads.source(id,uri);
                if(!source.isEmpty())appDownloads.resume(id,uri,CookieManager.getInstance().getCookie(source),web.getSettings().getUserAgentString(),"restart".equals(action));
            }
            publishDownloads();return;
        }
        if("pause".equals(action)){pauseLegacyDownloads(id);return;}
        org.json.JSONArray rows=storedDownloads();
        for(int i=0;i<rows.length();i++)try{
            org.json.JSONObject item=rows.getJSONObject(i);
            if(!item.optString("id").equals(id)||!item.optString("origin").equals(currentSite.toString()))continue;
            DownloadManager manager=(DownloadManager)getSystemService(DOWNLOAD_SERVICE);long number=Long.parseLong(id);
            if(item.optBoolean("legacyPaused")&&("restart".equals(action)||"resume".equals(action))) {
                String source=item.optString("sourceURL");
                appDownloads.start(source,item.optString("name"),CookieManager.getInstance().getCookie(source),web.getSettings().getUserAgentString());
                rows.remove(i);preferences.edit().putString("download_records",rows.toString()).apply();break;
            }
            if("delete".equals(action)) {
                try(android.database.Cursor cursor=manager.query(new DownloadManager.Query().setFilterById(number))) {
                    if(cursor!=null&&cursor.moveToFirst()) {
                        int status=cursor.getInt(cursor.getColumnIndexOrThrow(DownloadManager.COLUMN_STATUS));
                        if(status!=DownloadManager.STATUS_SUCCESSFUL&&status!=DownloadManager.STATUS_FAILED) return;
                    }
                }
                rows.remove(i);
                preferences.edit().putString("download_records",rows.toString()).apply();
            }
            if("cancel".equals(action))manager.remove(number);
            if("save".equals(action)){
                Uri file=manager.getUriForDownloadedFile(number);
                if(file==null)throw new Exception();
                String type=manager.getMimeTypeForDownloadedFile(number);
                preferences.edit().putString("pending_download_export", file.toString()).apply();
                startActivityForResult(new Intent(Intent.ACTION_CREATE_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE)
                    .setType(type==null?"application/octet-stream":type).putExtra(Intent.EXTRA_TITLE,item.getString("name")), 704);
            }
            break;
        }catch(Exception error){Toast.makeText(this,"无法保存文件，请重试",Toast.LENGTH_LONG).show();}
        publishDownloads();
    }

    private boolean isCurrentOrigin(Uri uri) {
        if (currentSite == null || !"https".equalsIgnoreCase(uri.getScheme())
            || uri.getUserInfo() != null || uri.getHost() == null) return false;
        return currentSite.getHost().equalsIgnoreCase(uri.getHost())
            && (currentSite.getPort() < 0 ? 443 : currentSite.getPort())
            == (uri.getPort() < 0 ? 443 : uri.getPort());
    }

    private void openExternal(Uri uri) {
        String scheme = uri.getScheme();
        if (scheme == null || !(scheme.equals("https") || scheme.equals("http")
            || scheme.equals("mailto") || scheme.equals("tel"))) {
            Toast.makeText(this, "请在电脑端打开此链接", Toast.LENGTH_SHORT).show();
            return;
        }
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, uri).addCategory(Intent.CATEGORY_BROWSABLE));
        } catch (android.content.ActivityNotFoundException error) {
            Toast.makeText(this, "未找到可以打开此链接的应用", Toast.LENGTH_SHORT).show();
        }
    }

    private void showFailure(String message) {
        findViewById(R.id.scan).setVisibility(View.VISIBLE);
        pageFailed = true;
        updateHomeConnection("failed");
        progress.setVisibility(View.INVISIBLE);
        ((TextView) findViewById(R.id.connection_error)).setText(message);
        findViewById(R.id.error_panel).setVisibility(View.VISIBLE);
    }

    private void reload() {
        if (currentSite == null) return;
        pageFailed = false;
        findViewById(R.id.error_panel).setVisibility(View.GONE);
        progress.setVisibility(View.VISIBLE);
        web.loadUrl(currentSite.toString());
    }

    private void closeChromePort() {
        if (chromePort != null) { chromePort.close(); chromePort = null; }
    }

    private void connectChromePort() {
        closeChromePort();
        if (web.getUrl() == null || !isCurrentOrigin(Uri.parse(web.getUrl()))) return;
        android.webkit.WebMessagePort[] ports = web.createWebMessageChannel();
        chromePort = ports[0];
        URI site = currentSite;
        ports[0].setWebMessageCallback(new android.webkit.WebMessagePort.WebMessageCallback() {
            @Override public void onMessage(android.webkit.WebMessagePort port, android.webkit.WebMessage message) {
                if (port != chromePort || !site.equals(currentSite) || web.getUrl() == null || !isCurrentOrigin(Uri.parse(web.getUrl()))) return;
                try {
                    org.json.JSONObject body = new org.json.JSONObject(message.getData());
                    if (body.has("connection")) updateHomeConnection(body.getString("connection"));
                    if (body.has("action")) handleHomeAction(body.getString("action"));
                } catch (org.json.JSONException ignored) {}
            }
        });
        String origin = site.getScheme() + "://" + site.getRawAuthority();
        web.postWebMessage(new android.webkit.WebMessage("assistantChrome", new android.webkit.WebMessagePort[]{ports[1]}), Uri.parse(origin));
    }

    private void updateHomeConnection(String state) {
        boolean visible = !"idle".equals(state) && findViewById(R.id.setup).getVisibility() != View.VISIBLE;
        findViewById(R.id.home_connection).setVisibility(visible ? View.VISIBLE : View.GONE);
        boolean connecting = "connecting".equals(state), connected = "connected".equals(state);
        findViewById(R.id.home_spinner).setVisibility(connecting ? View.VISIBLE : View.GONE);
        TextView label = findViewById(R.id.home_connection_text);
        label.setText(connecting ? "连接中" : connected ? "● 已连接" : "● 未连接");
        label.setTextColor(Color.parseColor(connected ? "#27824e" : connecting ? "#557aa7" : "#a7742e"));
    }

    private void showMenu() {
        if (!pageFailed && chromePort != null && findViewById(R.id.setup).getVisibility() != View.VISIBLE && web.getUrl() != null && isCurrentOrigin(Uri.parse(web.getUrl()))) {
            String script = "window.openAssistantMenu?.({native:true,notifications:" + preferences.getBoolean("task_notifications",false) + "}) === true";
            web.evaluateJavascript(script, value -> { if (!"true".equals(value)) showFallbackMenu(); });
        } else showFallbackMenu();
    }

    private void showFallbackMenu() {
        new AlertDialog.Builder(this).setTitle("连接菜单")
            .setItems(new String[]{"修改连接地址", "重新加载页面", "清除本机登录数据", preferences.getBoolean("task_notifications",false)?"关闭后台任务通知":"开启后台任务通知"},
                (dialog, which) -> handleHomeAction(new String[]{"address", "reload", "clear", "notifications"}[which])).show();
    }

    private void handleHomeAction(String action) {
        if ("address".equals(action)) showSetup(true);
        if ("reload".equals(action)) new AlertDialog.Builder(this).setMessage("重新加载会重建当前页面，是否继续？")
            .setNegativeButton("取消", null).setPositiveButton("重新加载", (d, w) -> reload()).show();
        if ("clear".equals(action)) new AlertDialog.Builder(this)
            .setMessage("清除本 App 所有地址的登录状态和网页缓存，下次需重新登录。电脑上的聊天不会删除。")
            .setNegativeButton("取消", null).setPositiveButton("清除", (d, w) -> clearLogin()).show();
        if ("notifications".equals(action)) {
            if (preferences.getBoolean("task_notifications",false)) {
                preferences.edit().putBoolean("task_notifications",false).apply();stopService(new Intent(this,TaskNotificationService.class));
            } else new AlertDialog.Builder(this).setTitle("后台任务通知").setMessage("开启后会显示常驻通知，检查电脑上的任务。请允许后台运行；省电、断网、强制关闭或系统时限可能停止提醒。")
                .setNegativeButton("取消",null).setPositiveButton("开启",(d,w)->{if(Build.VERSION.SDK_INT>=33&&checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)!=PackageManager.PERMISSION_GRANTED)requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS},42);else enableTaskNotifications();}).show();
        }
    }

    private void clearLogin() {
        preferences.edit().putBoolean("task_notifications",false).apply();
        stopService(new Intent(this,TaskNotificationService.class));
        web.stopLoading();
        web.loadUrl("about:blank");
        CookieManager.getInstance().removeAllCookies(removed -> {
            CookieManager.getInstance().flush();
            WebStorage.getInstance().deleteAllData();
            web.clearCache(true);
            web.clearHistory();
            // Keep the blank page closed until the user explicitly reconnects.
            currentSite = null;
            findViewById(R.id.scan).setVisibility(View.VISIBLE);
            ((TextView) findViewById(R.id.site_title)).setText("Codex 手机助手");
            findViewById(R.id.menu).setVisibility(View.GONE);
            showSetup(true);
            Toast.makeText(this, "已清除本机登录数据", Toast.LENGTH_SHORT).show();
        });
    }

    @Override protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == 704) {
            String source = preferences.getString("pending_download_export", null);
            preferences.edit().remove("pending_download_export").apply();
            if (resultCode == RESULT_OK && data != null && data.getData() != null && source != null) {
                Uri destination = data.getData();
                Toast.makeText(this,"正在保存文件…",Toast.LENGTH_SHORT).show();
                new Thread(() -> {
                    String message = "文件已保存到所选位置";
                    try (java.io.InputStream input = getContentResolver().openInputStream(Uri.parse(source));
                         java.io.OutputStream output = getContentResolver().openOutputStream(destination, "wt")) {
                        if (input == null || output == null) throw new java.io.IOException();
                        byte[] buffer = new byte[256 * 1024]; int count;
                        while ((count = input.read(buffer)) != -1) output.write(buffer, 0, count);
                    } catch (Exception error) { message = "保存失败，App 内的文件仍保留，请重试"; }
                    final String result = message;
                    runOnUiThread(() -> Toast.makeText(this,result,Toast.LENGTH_LONG).show());
                }).start();
            }
            return;
        }
        IntentResult scan = IntentIntegrator.parseActivityResult(requestCode, resultCode, data);
        if (scan != null) {
            if (data != null && data.hasExtra(QrScannerActivity.ERROR_EXTRA))
                showScannerError(data.getStringExtra(QrScannerActivity.ERROR_EXTRA));
            else if (scan.getContents() != null) applyScannedAddress(scan.getContents());
            return;
        }
        if (requestCode != PICK_IMAGES || imageCallback == null) return;
        ArrayList<Uri> images = new ArrayList<>();
        if (resultCode == RESULT_OK && data != null) {
            ClipData clip = data.getClipData();
            if (clip != null) {
                for (int index = 0; index < clip.getItemCount() && images.size() < (imageSelectionOnly ? 4 : 20); index++)
                    addImage(images, clip.getItemAt(index).getUri());
            } else addImage(images, data.getData());
        }
        imageCallback.onReceiveValue(images.isEmpty() ? null : images.toArray(new Uri[0]));
        imageCallback = null;
    }

    private void addImage(ArrayList<Uri> images, Uri uri) {
        if (uri == null || !"content".equals(uri.getScheme())) return;
        try {
            String type = getContentResolver().getType(uri);
            if (!imageSelectionOnly || (type != null && type.startsWith("image/"))) images.add(uri);
        } catch (SecurityException ignored) {
            Toast.makeText(this, "无法读取此文件，请重新选择", Toast.LENGTH_SHORT).show();
        }
    }

    private void enableTaskNotifications() {
        if(currentSite==null){Toast.makeText(this,"请先连接并登录助手",Toast.LENGTH_LONG).show();return;}
        if(!((android.app.NotificationManager)getSystemService(NOTIFICATION_SERVICE)).areNotificationsEnabled()){
            Toast.makeText(this,"请在系统设置中允许助手通知",Toast.LENGTH_LONG).show();return;
        }
        try{startForegroundService(new Intent(this,TaskNotificationService.class).putExtra("origin",currentSite.toString()));
            preferences.edit().putBoolean("task_notifications",true).apply();}
        catch(Exception error){Toast.makeText(this,"后台通知未能启动，请保持助手在前台后重试",Toast.LENGTH_LONG).show();}
    }
    @Override protected void onResume(){super.onResume();TaskNotificationService.visible=true;if(appDownloads!=null)appDownloads.setForeground(true);}

    @Override protected void onPause() {
        TaskNotificationService.visible=false;
        CookieManager.getInstance().flush();
        super.onPause();
    }

    @Override protected void onStop() {
        if(appDownloads!=null)appDownloads.setForeground(false);
        if(preferences!=null)pauseLegacyDownloads(null);
        super.onStop();
    }

    private void handleBack() {
        if (findViewById(R.id.setup).getVisibility() == View.VISIBLE) {
            if (currentSite != null) showSetup(false);
            else moveTaskToBack(true);
            return;
        }
        web.evaluateJavascript(
            "(function(){const app=document.getElementById('app');const back=document.getElementById('back');"
            + "if(app&&app.classList.contains('chat-open')&&back){back.click();return true;}return false;})()",
            handled -> {
                if ("true".equals(handled)) return;
                if (web.canGoBack()) web.goBack();
                else showSetup(true);
            });
    }

    @SuppressWarnings("deprecation")
    @Override public void onBackPressed() { handleBack(); }

    @Override protected void onDestroy() {
        closeChromePort();
        if (imageCallback != null) imageCallback.onReceiveValue(null);
        web.stopLoading();
        web.destroy();
        super.onDestroy();
    }
}
