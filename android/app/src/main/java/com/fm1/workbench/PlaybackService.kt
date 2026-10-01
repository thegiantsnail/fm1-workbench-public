package com.fm1.workbench

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.IBinder
import android.os.PowerManager

/**
 * Foreground service that keeps the app (engine clock thread, MIDI, loopers) running with the screen off while
 * something plays or records. Started/stopped by AppModel; the notification's Stop action stops everything.
 */
class PlaybackService : Service() {
    private var wake: PowerManager.WakeLock? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) { onStop?.invoke(); stopSelf(); return START_NOT_STICKY }
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CHANNEL, "Playback", NotificationManager.IMPORTANCE_LOW))
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE)
        val stop = PendingIntent.getService(this, 1, Intent(this, PlaybackService::class.java).setAction(ACTION_STOP), PendingIntent.FLAG_IMMUTABLE)
        val n = Notification.Builder(this, CHANNEL)
            .setSmallIcon(android.R.drawable.ic_media_play)
            .setContentTitle("FM-1 Workbench")
            .setContentText("Playing on the FM-1")
            .setContentIntent(open)
            .addAction(Notification.Action.Builder(null, "Stop", stop).build())
            .setOngoing(true)
            .build()
        val mic = intent?.getBooleanExtra(EXTRA_MIC, false) == true
        val type = ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK or (if (mic) ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE else 0)
        runCatching { startForeground(ID, n, type) }.onFailure { startForeground(ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK) }
        if (wake == null) wake = getSystemService(PowerManager::class.java)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "fm1:playback").apply { acquire(4 * 60 * 60 * 1000L) }
        return START_NOT_STICKY
    }

    override fun onDestroy() { wake?.let { if (it.isHeld) it.release() }; wake = null; super.onDestroy() }

    companion object {
        const val CHANNEL = "playback"
        const val ID = 1
        const val ACTION_STOP = "com.fm1.workbench.STOP"
        const val EXTRA_MIC = "mic"
        @Volatile var onStop: (() -> Unit)? = null
    }
}
