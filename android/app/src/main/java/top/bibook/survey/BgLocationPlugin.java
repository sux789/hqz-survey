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
 * 本插件同时注册 GPS_PROVIDER 与 NETWORK_PROVIDER：
 * - GPS 源返回 WGS-84 坐标（provider="gps"）
 * - 网络源（HMS/基站/WiFi）返回 GCJ-02 坐标（provider="network"），随点携带 provider，
 *   JS 侧 _gpsFix 按 provider 精准纠偏（优于 accuracy 启发式）
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
    private long lastGpsTime = 0L;  // 最近一次 GPS 点时间：GPS 新鲜时网络点不转发（精度策略）
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
                // 精度策略（GPS 优先）：
                // 1) GPS 点照发并刷新 lastGpsTime；
                // 2) 网络点仅当 GPS 超过 10 秒无点时才转发（低精度网络点与 GPS 点
                //    交叉混采是轨迹锯齿的主因）；基站粗定位(>500m)直接丢弃。
                boolean fromGps = LocationManager.GPS_PROVIDER.equals(location.getProvider());
                if (fromGps) {
                    lastGpsTime = System.currentTimeMillis();
                } else {
                    if (System.currentTimeMillis() - lastGpsTime < 10_000L) return;
                    if (location.hasAccuracy() && location.getAccuracy() > 500f) return;
                }
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
        boolean any = false;
        try {
            // minDistance=2m：抑制静止/慢行时的 GPS 噪声抖点（每秒一个 5~30m 随机
            // 偏移点会让静止轨迹成毛团）；行走速度下每 1~2 秒仍有一个真实位移点
            locationManager.requestLocationUpdates(
                    LocationManager.GPS_PROVIDER, 1000, 2f, listener, Looper.getMainLooper());
            any = true;
        } catch (SecurityException e) {
            call.reject("定位权限未授予", "NOT_AUTHORIZED");
            cleanup();
            return;
        } catch (IllegalArgumentException ignore) {
            // 个别设备无 GPS provider：网络源仍可用
        }
        try {
            locationManager.requestLocationUpdates(
                    LocationManager.NETWORK_PROVIDER, 1000, 0f, listener, Looper.getMainLooper());
            any = true;
        } catch (Exception ignore) {
            // NETWORK provider 由 HMS 定位实现；缺失则仅 GPS
        }
        if (!any) {
            call.reject("设备无可用定位源", "NO_PROVIDER");
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
