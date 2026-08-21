package top.bibook.survey;

import android.Manifest;
import android.content.ContentValues;
import android.content.Intent;
import android.database.Cursor;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import android.provider.MediaStore;
import android.provider.Settings;
import android.util.Base64;

import com.getcapacitor.JSObject;
import com.getcapacitor.PermissionState;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;
import com.getcapacitor.annotation.PermissionCallback;

import java.io.File;
import java.io.FileOutputStream;
import java.io.OutputStream;

/**
 * 原生能力插件，供 WebView 内的页面调用：
 *   - check({type})         查询定位/相机权限状态
 *   - request({type})       申请定位/相机权限
 *   - openSettings()        打开本 App 的系统权限设置页
 *   - savePhoto({base64,name,subdir})  照片写入系统相册 Pictures/{subdir}/（多级子目录，如
 *                                      2022年度/人工造林/1号调查小班），返回真实绝对路径
 *   - saveFile({base64,name})   导出文件（xlsx/zip）写入公共下载 Download/验收导出/，返回真实绝对路径
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

    /**
     * 照片写入系统相册 Pictures/{subdir}/，返回真实绝对路径。
     * subdir 为多级子目录（如 "2022年度/人工造林/1号调查小班"），缺省 "验收照片"；
     * 每段自动清理文件系统非法字符并防路径穿越（. / ..）。
     * Android 10+ 走 MediaStore（自有媒体免存储权限且立即可见于相册，多级目录自动创建）；
     * 旧版本回退应用外部私有目录。
     */
    @PluginMethod
    public void savePhoto(PluginCall call) {
        String base64 = call.getString("base64");
        String name = call.getString("name", "photo.jpg");
        if (base64 == null || base64.isEmpty()) {
            call.reject("缺少照片数据");
            return;
        }
        String subdir = sanitizeSubdir(call.getString("subdir", "验收照片"));
        String mime = name.toLowerCase().endsWith(".png") ? "image/png" : "image/jpeg";
        try {
            byte[] bytes = Base64.decode(base64, Base64.DEFAULT);
            ContentValues values = new ContentValues();
            values.put(MediaStore.Images.Media.DISPLAY_NAME, name);
            values.put(MediaStore.Images.Media.MIME_TYPE, mime);
            Uri uri;
            String realPath;
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                values.put(MediaStore.Images.Media.RELATIVE_PATH,
                        Environment.DIRECTORY_PICTURES + "/" + subdir);
                uri = getContext().getContentResolver().insert(
                        MediaStore.Images.Media.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY), values);
                if (uri == null) {
                    call.reject("创建相册记录失败");
                    return;
                }
                try (OutputStream os = getContext().getContentResolver().openOutputStream(uri)) {
                    os.write(bytes);
                    os.flush();
                }
                realPath = queryFileRealPath(uri, new File(new File(
                        Environment.getExternalStorageDirectory(),
                        Environment.DIRECTORY_PICTURES), subdir));
            } else {
                File dir = new File(getContext().getExternalFilesDir(Environment.DIRECTORY_PICTURES), subdir);
                if (!dir.exists()) dir.mkdirs();
                File f = new File(dir, name);
                try (FileOutputStream fos = new FileOutputStream(f)) {
                    fos.write(bytes);
                    fos.flush();
                }
                uri = Uri.fromFile(f);
                realPath = f.getAbsolutePath();
            }
            JSObject ret = new JSObject();
            ret.put("path", realPath);
            ret.put("uri", uri.toString());
            call.resolve(ret);
        } catch (Exception e) {
            call.reject("保存照片失败: " + e.getMessage(), e);
        }
    }

    /** 清理子目录串：去每段非法字符、空段与路径穿越（. / ..），异常回退「验收照片」。 */
    private String sanitizeSubdir(String raw) {
        if (raw == null || raw.trim().isEmpty()) return "验收照片";
        String[] segs = raw.split("/");
        StringBuilder sb = new StringBuilder();
        for (String s : segs) {
            String t = s.trim().replaceAll("[\\\\/:*?\"<>|\\s]+", "_");
            if (t.isEmpty() || t.equals(".") || t.equals("..")) continue;
            if (t.startsWith("_") && t.length() > 1) t = t.substring(1);
            if (sb.length() > 0) sb.append('/');
            sb.append(t);
        }
        return sb.length() > 0 ? sb.toString() : "验收照片";
    }

    /**
     * 导出文件（xlsx/zip）写入公共下载目录 Download/验收导出/，返回真实绝对路径。
     * Android 10+ 走 MediaStore Downloads（自有文件免存储权限，文件管理器立即可见）；
     * 旧版本回退应用外部私有目录。
     */
    @PluginMethod
    public void saveFile(PluginCall call) {
        String base64 = call.getString("base64");
        String name = call.getString("name", "export.bin");
        if (base64 == null || base64.isEmpty()) {
            call.reject("缺少文件数据");
            return;
        }
        String lower = name.toLowerCase();
        String mime;
        if (lower.endsWith(".xlsx")) {
            mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
        } else if (lower.endsWith(".zip")) {
            mime = "application/zip";
        } else {
            mime = "application/octet-stream";
        }
        try {
            byte[] bytes = Base64.decode(base64, Base64.DEFAULT);
            Uri uri;
            String realPath;
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                ContentValues values = new ContentValues();
                values.put(MediaStore.Downloads.DISPLAY_NAME, name);
                values.put(MediaStore.Downloads.MIME_TYPE, mime);
                values.put(MediaStore.Downloads.RELATIVE_PATH,
                        Environment.DIRECTORY_DOWNLOADS + "/验收导出");
                uri = getContext().getContentResolver().insert(
                        MediaStore.Downloads.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY), values);
                if (uri == null) {
                    call.reject("创建下载记录失败");
                    return;
                }
                try (OutputStream os = getContext().getContentResolver().openOutputStream(uri)) {
                    os.write(bytes);
                    os.flush();
                }
                realPath = queryFileRealPath(uri, new File(new File(
                        Environment.getExternalStorageDirectory(),
                        Environment.DIRECTORY_DOWNLOADS), "验收导出"));
            } else {
                File dir = new File(getContext().getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS), "验收导出");
                if (!dir.exists()) dir.mkdirs();
                File f = new File(dir, name);
                try (FileOutputStream fos = new FileOutputStream(f)) {
                    fos.write(bytes);
                    fos.flush();
                }
                uri = Uri.fromFile(f);
                realPath = f.getAbsolutePath();
            }
            JSObject ret = new JSObject();
            ret.put("path", realPath);
            ret.put("uri", uri.toString());
            call.resolve(ret);
        } catch (Exception e) {
            call.reject("保存文件失败: " + e.getMessage(), e);
        }
    }

    /** 查询 MediaStore 记录的真实文件路径（DATA 列），失败时返回 fallbackDir。 */
    private String queryFileRealPath(Uri uri, File fallbackDir) {
        try (Cursor c = getContext().getContentResolver().query(
                uri, new String[]{MediaStore.MediaColumns.DATA}, null, null, null)) {
            if (c != null && c.moveToFirst()) {
                int idx = c.getColumnIndex(MediaStore.MediaColumns.DATA);
                if (idx >= 0) {
                    String p = c.getString(idx);
                    if (p != null && !p.isEmpty()) return p;
                }
            }
        } catch (Exception ignored) {
        }
        return fallbackDir.getAbsolutePath();
    }
}
