package top.bibook.survey;

import android.Manifest;
import android.content.Intent;
import android.net.Uri;
import android.provider.Settings;

import com.getcapacitor.JSObject;
import com.getcapacitor.PermissionState;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;
import com.getcapacitor.annotation.PermissionCallback;

/**
 * 权限检测/申请/跳设置 插件，供 WebView 内的页面调用：
 *   - check({type})       查询定位/相机权限状态
 *   - request({type})     申请定位/相机权限
 *   - openSettings()      打开本 App 的系统权限设置页
 * type: 'location' | 'camera'
 */
@CapacitorPlugin(
    name = "AppPermissions",
    permissions = {
        @Permission(alias = "location", strings = {
            Manifest.permission.ACCESS_COARSE_LOCATION,
            Manifest.permission.ACCESS_FINE_LOCATION
        }),
        @Permission(alias = "camera", strings = {
            Manifest.permission.CAMERA
        })
    }
)
public class AppPermissionsPlugin extends Plugin {

    private String resolveAlias(String type) {
        return "camera".equals(type) ? "camera" : "location";
    }

    @PluginMethod
    public void check(PluginCall call) {
        String type = call.getString("type", "location");
        PermissionState state = getPermissionState(resolveAlias(type));
        if (state == null) {
            state = PermissionState.DENIED;
        }
        JSObject ret = new JSObject();
        ret.put("type", resolveAlias(type));
        ret.put("state", state.toString());
        ret.put("granted", state == PermissionState.GRANTED);
        call.resolve(ret);
    }

    @PluginMethod
    public void request(PluginCall call) {
        String type = call.getString("type", "location");
        String alias = resolveAlias(type);
        saveCall(call);
        requestPermissionForAlias(alias, call, "permCallback");
    }

    @PermissionCallback
    private void permCallback(PluginCall call) {
        String type = call.getString("type", "location");
        PermissionState state = getPermissionState(resolveAlias(type));
        if (state == null) {
            state = PermissionState.DENIED;
        }
        JSObject ret = new JSObject();
        ret.put("type", resolveAlias(type));
        ret.put("state", state.toString());
        ret.put("granted", state == PermissionState.GRANTED);
        call.resolve(ret);
    }

    @PluginMethod
    public void openSettings(PluginCall call) {
        try {
            Intent intent = new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS);
            intent.setData(Uri.parse("package:" + getContext().getPackageName()));
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            getContext().startActivity(intent);
            call.resolve();
        } catch (Exception e) {
            call.reject("无法打开系统设置", e);
        }
    }
}
