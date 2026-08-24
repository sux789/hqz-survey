package top.bibook.survey;

import android.Manifest;
import android.content.Context;
import android.content.Intent;
import android.location.Location;
import android.location.LocationListener;
import android.location.LocationManager;
import android.os.Bundle;
import android.os.Looper;

import com.getcapacitor.JSObject;
import com.getcapacitor.PermissionState;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;
import com.getcapacitor.annotation.PermissionCallback;

import org.json.JSONObject;

/**
 * 自研后台定位插件（平台 LocationManager，无 Google Play Services 依赖）。
 *
 * 背景：生产设备为无 GMS 的华为/鸿蒙，社区插件
 * @capacitor-community/background-geolocation 依赖 gms FusedLocationProviderClient，
 * 在该类设备上静默无回调（前台服务在跑、通知在，但定位点一个不来）。
 *
 * 本插件仅注册 GPS_PROVIDER（纯 GPS 策略，2026-08-25 用户决策去掉网络兜底）：
 * - GPS 源返回 WGS-84 坐标（provider="gps"），无需纠偏
 * - 网络源（HMS/基站/WiFi，GCJ-02 且纠偏后绝对精度仍 50~1000m）不采集——
 *   宁可 GPS 无 fix 期间轨迹空白，不记偏点
 * 前台服务（foregroundServiceType=location）保住进程优先级，灭屏/后台持续采集。
 */
@CapacitorPlugin(
        name = "BgLocation",
        permissions = {
                @Permission(
                        strings = { Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION },
                        alias = "location"
                )
        }
)
public class BgLocationPlugin extends Plugin {
    private LocationManager locationManager;
    private LocationListener listener;
    private PluginCall watcherCall;
    // 授权弹窗期间用户点了停止：置 true，授权回调到达后不再启动采集（防通知/服务泄漏）
    private boolean wantsStop = false;

    @PluginMethod(returnType = PluginMethod.RETURN_CALLBACK)
    public void startWatcher(PluginCall call) {
        if (watcherCall != null) {
            call.reject("已有轨迹记录在进行");
            return;
        }
        wantsStop = false;
        if (getPermissionState("location") != PermissionState.GRANTED) {
            requestPermissionForAlias("location", call, "locationPermissionsCallback");
            return;
        }
        begin(call);
    }

    @PermissionCallback
    private void locationPermissionsCallback(PluginCall call) {
        if (getPermissionState("location") != PermissionState.GRANTED) {
            call.reject("定位权限未授予", "NOT_AUTHORIZED");
            return;
        }
        if (wantsStop) {
            return;  // 等待授权期间已停止：丢弃本次启动
        }
        begin(call);
    }

    private void begin(PluginCall call) {
        call.setKeepAlive(true);
        watcherCall = call;
        locationManager = (LocationManager) getContext().getSystemService(Context.LOCATION_SERVICE);
        if (locationManager == null) {
            call.reject("设备无定位服务", "NO_PROVIDER");
            cleanup();
            return;
        }
        listener = new LocationListener() {
            @Override
            public void onLocationChanged(Location location) {
                if (watcherCall == null) return;
                // 仅 GPS 源注册，此回调必为 GPS 点（WGS-84，无需纠偏）
                JSObject o = new JSObject();
                o.put("longitude", location.getLongitude());
                o.put("latitude", location.getLatitude());
                o.put("accuracy", location.hasAccuracy() ? location.getAccuracy() : JSONObject.NULL);
                o.put("provider", location.getProvider());
                o.put("time", location.getTime());
                watcherCall.resolve(o);
            }
            @Override public void onStatusChanged(String provider, int status, Bundle extras) {}
            @Override public void onProviderEnabled(String provider) {}
            @Override public void onProviderDisabled(String provider) {}
        };
        try {
            // 纯 GPS 采集（无网络兜底）：GPS 无 fix 期间（冷启动 30s~几分钟/深林遮挡）
            // 不出点，宁可轨迹空白也不记偏点；minDistance=2m 抑制静止抖动毛团，
            // 行走速度下每 1~2 秒仍有一个真实位移点
            locationManager.requestLocationUpdates(
                    LocationManager.GPS_PROVIDER, 1000, 2f, listener, Looper.getMainLooper());
        } catch (SecurityException e) {
            call.reject("定位权限未授予", "NOT_AUTHORIZED");
            cleanup();
            return;
        } catch (IllegalArgumentException e) {
            call.reject("设备无 GPS 定位源", "NO_PROVIDER");
            cleanup();
            return;
        }
        Intent svc = new Intent(getContext(), BgLocationService.class);
        svc.putExtra("title", call.getString("title", "轨迹记录中"));
        svc.putExtra("message", call.getString("message", "正在后台记录调查轨迹"));
        try {
            androidx.core.content.ContextCompat.startForegroundService(getContext(), svc);
        } catch (Exception ignore) {
            // 前台服务启动失败不阻塞：前台采集仍有效，仅后台可能被冻结
        }
    }

    @PluginMethod
    public void stopWatcher(PluginCall call) {
        wantsStop = true;
        cleanup();
        try {
            getContext().stopService(new Intent(getContext(), BgLocationService.class));
        } catch (Exception ignore) {}
        call.resolve();
    }

    private void cleanup() {
        if (locationManager != null && listener != null) {
            try { locationManager.removeUpdates(listener); } catch (Exception ignore) {}
        }
        locationManager = null;
        listener = null;
        if (watcherCall != null) {
            watcherCall.release(getBridge());
            watcherCall = null;
        }
    }

    @Override
    protected void handleOnDestroy() {
        wantsStop = true;
        cleanup();
        try {
            getContext().stopService(new Intent(getContext(), BgLocationService.class));
        } catch (Exception ignore) {}
    }
}
