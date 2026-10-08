package app.ritm.engine

import android.content.Context
import android.os.VibrationEffect
import android.os.VibratorManager

/** Язык вибраций: можно не смотреть в экран. */
object Haptics {
    private fun vibrate(context: Context, timings: LongArray, amplitudes: IntArray) {
        val vm = context.getSystemService(Context.VIBRATOR_MANAGER_SERVICE) as VibratorManager
        vm.defaultVibrator.vibrate(VibrationEffect.createWaveform(timings, amplitudes, -1))
    }

    /** Нажатие принято. */
    fun short(context: Context) = vibrate(context, longArrayOf(0, 30), intArrayOf(0, 180))
    /** Записано. */
    fun double(context: Context) = vibrate(context, longArrayOf(0, 35, 90, 35), intArrayOf(0, 200, 0, 200))
    /** Отдых закончился. */
    fun three(context: Context) = vibrate(context, longArrayOf(0, 45, 110, 45, 110, 45), intArrayOf(0, 255, 0, 255, 0, 255))
    /** Не получилось. */
    fun long(context: Context) = vibrate(context, longArrayOf(0, 350), intArrayOf(0, 160))
}
