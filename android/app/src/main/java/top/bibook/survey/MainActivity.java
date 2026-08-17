package top.bibook.survey;

import android.os.Bundle;
import android.webkit.WebResourceRequest;
import android.webkit.WebView;

import com.getcapacitor.BridgeActivity;
import com.getcapacitor.BridgeWebViewClient;

public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(AppPermissionsPlugin.class);
        super.onCreate(savedInstanceState);

        // 强制所有导航留在 APP WebView 内，不调起系统浏览器（鸿蒙4.2会把
        // 登录流程中的 http 降级重定向交给外部浏览器打开）。
        // 关键：继承 Capacitor 的 BridgeWebViewClient，保留其本地资源拦截、
        // bridge 重置等全部行为，仅覆盖外跳判断。
        try {
            if (bridge != null && bridge.getWebView() != null) {
                bridge.getWebView().setWebViewClient(new BridgeWebViewClient(bridge) {
                    @Override
                    public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                        return false; // 一律在 WebView 内加载
                    }

                    @Override
                    public boolean shouldOverrideUrlLoading(WebView view, String url) {
                        return false; // 一律在 WebView 内加载
                    }
                });
            }
        } catch (Exception e) {
            android.util.Log.e("HqzSurvey", "setWebViewClient failed", e);
        }
    }
}
