package top.bibook.survey;

import android.os.Bundle;
import android.webkit.WebResourceRequest;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.util.Log;

import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    private static final String TAG = "HqzSurvey";

    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(AppPermissionsPlugin.class);
        super.onCreate(savedInstanceState);

        // 强制所有 URL 在 APP WebView 内打开，避免鸿蒙等系统把页面跳转交给外部浏览器
        try {
            if (bridge != null && bridge.getWebView() != null) {
                WebView webView = bridge.getWebView();
                webView.setWebViewClient(new WebViewClient() {
                    @Override
                    public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                        // 返回 false 表示在当前 WebView 内加载，不交给系统浏览器
                        Log.d(TAG, "in-app load: " + request.getUrl());
                        return false;
                    }
                    @Override
                    public boolean shouldOverrideUrlLoading(WebView view, String url) {
                        Log.d(TAG, "in-app load(url): " + url);
                        return false;
                    }
                });
            }
        } catch (Exception e) {
            Log.e(TAG, "setWebViewClient failed", e);
        }
    }
}
