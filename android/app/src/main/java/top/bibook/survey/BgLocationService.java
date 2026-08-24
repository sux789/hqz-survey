package top.bibook.survey;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.IBinder;

import androidx.core.app.NotificationCompat;
import androidx.core.app.ServiceCompat;

/**
 * 后台定位前台服务壳：仅负责常驻通知（foregroundServiceType=location）保住
 * 进程优先级（不被系统冻结/回收），定位采集在 BgLocationPlugin（同进程）内进行。
 * 通知点击回到应用主界面。
 */
public class BgLocationService extends Service {
    private static final String CHANNEL_ID = "bg_location";
    private static final int NOTIFICATION_ID = 28352;

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        String title = (intent != null && intent.getStringExtra("title") != null)
                ? intent.getStringExtra("title") : "轨迹记录中";
        String message = (intent != null && intent.getStringExtra("message") != null)
                ? intent.getStringExtra("message") : "正在后台记录调查轨迹";
        createChannel();
        PendingIntent pi = null;
        Intent launch = getPackageManager().getLaunchIntentForPackage(getPackageName());
        if (launch != null) {
            launch.addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT);
            pi = PendingIntent.getActivity(this, 0, launch,
                    PendingIntent.FLAG_CANCEL_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        }
        Notification n = new NotificationCompat.Builder(this, CHANNEL_ID)
                .setContentTitle(title)
                .setContentText(message)
                .setOngoing(true)
                .setPriority(NotificationCompat.PRIORITY_HIGH)
                .setWhen(System.currentTimeMillis())
                .setSmallIcon(R.mipmap.ic_launcher)
                .setContentIntent(pi)
                .build();
        try {
            // API 29+ 用 location 型前台服务：灭屏/后台持续定位的合法通道；
            // API 34+ 需 Manifest 声明 FOREGROUND_SERVICE_LOCATION（已声明，装机自动授予）
            ServiceCompat.startForeground(this, NOTIFICATION_ID, n,
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION);
        } catch (Exception e) {
            try { startForeground(NOTIFICATION_ID, n); } catch (Exception ignore) {}
        }
        return START_NOT_STICKY;
    }

    private void createChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationManager nm = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
            if (nm != null && nm.getNotificationChannel(CHANNEL_ID) == null) {
                nm.createNotificationChannel(new NotificationChannel(
                        CHANNEL_ID, "后台轨迹记录", NotificationManager.IMPORTANCE_HIGH));
            }
        }
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
