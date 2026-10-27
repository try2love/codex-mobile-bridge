package io.github.abyxs.codexmobile;

import android.app.Activity;
import android.content.Intent;
import android.os.Build;
import android.os.Bundle;
import android.util.Log;
import android.view.KeyEvent;
import android.view.View;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import com.journeyapps.barcodescanner.CaptureManager;
import com.journeyapps.barcodescanner.DecoratedBarcodeView;

public final class QrScannerActivity extends Activity {
    static final String ERROR_EXTRA = "codex_scanner_error";
    private CaptureManager capture;
    private DecoratedBarcodeView scanner;
    private boolean failed;

    @Override public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        try {
            setContentView(com.google.zxing.client.android.R.layout.zxing_capture);
            scanner = findViewById(com.google.zxing.client.android.R.id.zxing_barcode_scanner);
            capture = new CaptureManager(this, scanner);
            capture.initializeFromIntent(getIntent(), savedInstanceState);
            capture.decode();
            if (Build.VERSION.SDK_INT >= 30) Api30.applyInsets(this);
        } catch (RuntimeException | LinkageError error) {
            fail("初始化扫码", error);
        }
    }

    @Override protected void onResume() {
        super.onResume();
        if (capture == null || failed) return;
        try { capture.onResume(); }
        catch (RuntimeException | LinkageError error) { fail("启动相机", error); }
    }

    @Override protected void onPause() {
        // The Activity lifecycle must still complete if camera initialization failed.
        try { if (capture != null) capture.onPause(); }
        catch (RuntimeException | LinkageError error) { fail("释放相机", error); }
        finally { super.onPause(); }
    }

    @Override protected void onDestroy() {
        try { if (capture != null) capture.onDestroy(); }
        catch (RuntimeException | LinkageError error) { Log.e("CodexScanner", "Scanner cleanup failed", error); }
        finally { super.onDestroy(); }
    }

    @Override protected void onSaveInstanceState(Bundle outState) {
        super.onSaveInstanceState(outState);
        if (capture != null && !failed) capture.onSaveInstanceState(outState);
    }

    @Override public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(requestCode, permissions, results);
        if (capture == null || failed) return;
        try { capture.onRequestPermissionsResult(requestCode, permissions, results); }
        catch (RuntimeException | LinkageError error) { fail("获取相机权限", error); }
    }

    @Override public boolean onKeyDown(int keyCode, KeyEvent event) {
        return (!failed && scanner != null && scanner.onKeyDown(keyCode, event)) || super.onKeyDown(keyCode, event);
    }

    private void fail(String stage, Throwable error) {
        Log.e("CodexScanner", stage, error);
        if (failed) return;
        failed = true;
        setResult(RESULT_CANCELED, new Intent().putExtra(ERROR_EXTRA, errorDetails(stage, error)));
        finish();
    }

    static String errorDetails(String stage, Throwable error) {
        Throwable cause = error;
        for (int i = 0; i < 4 && cause.getCause() != null && cause.getCause() != cause; i++) cause = cause.getCause();
        String message = cause.getMessage();
        if (message == null) message = "";
        if (message.length() > 400) message = message.substring(0, 400);
        return stage + " · " + cause.getClass().getSimpleName() + "\n" + message
            + "\nAndroid " + Build.VERSION.RELEASE + " / " + Build.MANUFACTURER + " " + Build.MODEL;
    }

    // Keep API 30 classes out of the scanner lifecycle on older Android versions.
    private static final class Api30 {
        static void applyInsets(Activity activity) {
            View content = activity.findViewById(android.R.id.content);
            content.setOnApplyWindowInsetsListener((view, insets) -> {
                android.graphics.Insets bars = insets.getInsets(
                    WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout());
                view.setPadding(bars.left, bars.top, bars.right, bars.bottom);
                return insets;
            });
            content.requestApplyInsets();
            WindowInsetsController controller = activity.getWindow().getInsetsController();
            if (controller != null) controller.setSystemBarsAppearance(0,
                WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS
                | WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS);
        }
    }
}
