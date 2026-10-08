package app.ritm.engine

import android.Manifest
import android.app.AppOpsManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.PowerManager
import android.os.Process
import android.provider.Settings
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import app.ritm.button.ButtonService

/** Доступ, без которого что-то не соберётся. Текст — человеческим языком, одна фраза «зачем». */
enum class Access(val title: String, val why: String) {
    LOCATION("Геопозиция", "Чтобы узнавать свои места и где проходит время"),
    LOCATION_ALWAYS("Геопозиция всегда", "Чтобы места отмечались, даже когда приложение закрыто"),
    ACTIVITY("Физическая активность", "Чтобы считать шаги и понимать, стою я или иду"),
    MICROPHONE("Микрофон", "Чтобы записывать голосовые заметки кнопкой слева"),
    NOTIFICATIONS("Уведомления", "Чтобы в шторке был остаток калорий и напоминания"),
    USAGE("Статистика использования", "Чтобы знать экранное время"),
    BUTTON("Кнопка слева", "Один раз с компьютера — и кнопка Bixby работает для Ритма"),
    TOUCH("Касания", "Чтобы сон определялся по тому, трогаете ли вы телефон"),
    BATTERY("Работа в фоне", "Чтобы Samsung не усыплял сбор данных"),
}

object Permissions {
    fun has(context: Context, p: String) = ContextCompat.checkSelfPermission(context, p) == PackageManager.PERMISSION_GRANTED

    fun granted(context: Context, a: Access): Boolean = when (a) {
        Access.LOCATION -> has(context, Manifest.permission.ACCESS_FINE_LOCATION)
        Access.LOCATION_ALWAYS -> has(context, Manifest.permission.ACCESS_BACKGROUND_LOCATION)
        Access.ACTIVITY -> has(context, Manifest.permission.ACTIVITY_RECOGNITION)
        Access.MICROPHONE -> has(context, Manifest.permission.RECORD_AUDIO)
        Access.NOTIFICATIONS -> NotificationManagerCompat.from(context).areNotificationsEnabled()
        Access.USAGE -> usageGranted(context)
        Access.BUTTON -> app.ritm.button.canReadLogs(context)
        Access.TOUCH -> buttonServiceEnabled(context)
        Access.BATTERY -> (context.getSystemService(Context.POWER_SERVICE) as PowerManager).isIgnoringBatteryOptimizations(context.packageName)
    }

    fun missing(context: Context): List<Access> = Access.entries.filterNot { granted(context, it) }

    /** Разрешения, которые спрашиваются системным окном (остальные — через настройки). */
    fun runtimePermission(a: Access): String? = when (a) {
        Access.LOCATION -> Manifest.permission.ACCESS_FINE_LOCATION
        Access.LOCATION_ALWAYS -> Manifest.permission.ACCESS_BACKGROUND_LOCATION
        Access.ACTIVITY -> Manifest.permission.ACTIVITY_RECOGNITION
        Access.MICROPHONE -> Manifest.permission.RECORD_AUDIO
        else -> null
    }

    /** Куда вести человека, если системным окном не спросить. */
    fun settingsIntent(context: Context, a: Access): Intent = when (a) {
        Access.USAGE -> Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS)
        Access.TOUCH -> Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)
        Access.BATTERY -> Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:${context.packageName}"))
        Access.NOTIFICATIONS -> Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE, context.packageName)
        else -> Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:${context.packageName}"))
    }.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)

    private fun usageGranted(context: Context): Boolean {
        val ops = context.getSystemService(Context.APP_OPS_SERVICE) as AppOpsManager
        @Suppress("DEPRECATION")
        val mode = ops.unsafeCheckOpNoThrow(AppOpsManager.OPSTR_GET_USAGE_STATS, Process.myUid(), context.packageName)
        return mode == AppOpsManager.MODE_ALLOWED
    }

    private fun buttonServiceEnabled(context: Context): Boolean {
        val enabled = Settings.Secure.getString(context.contentResolver, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES) ?: return false
        val me = ComponentName(context, ButtonService::class.java)
        return enabled.split(':').any { ComponentName.unflattenFromString(it) == me }
    }
}
