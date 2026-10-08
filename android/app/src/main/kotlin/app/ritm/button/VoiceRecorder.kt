package app.ritm.button

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioDeviceInfo
import android.media.AudioFocusRequest
import android.media.AudioManager
import android.media.MediaRecorder
import java.io.File

/**
 * Голосовая заметка: ТОЛЬКО встроенный микрофон телефона, даже если подключены наушники.
 * Музыка на время записи приглушается. Во время звонка запись не начинается.
 */
class VoiceRecorder(private val context: Context) {
    private var recorder: MediaRecorder? = null
    private var file: File? = null
    private var startedAt = 0L
    private var focus: AudioFocusRequest? = null
    private var peak = 0

    val isRecording: Boolean get() = recorder != null

    fun inCall(): Boolean {
        val am = context.getSystemService(AudioManager::class.java)
        return am.mode == AudioManager.MODE_IN_CALL || am.mode == AudioManager.MODE_IN_COMMUNICATION || am.mode == AudioManager.MODE_RINGTONE
    }

    fun start(): Boolean {
        if (recorder != null || inCall()) return false
        val am = context.getSystemService(AudioManager::class.java)
        val dir = File(context.filesDir, "voice").apply { mkdirs() }
        val f = File(dir, "note-${System.currentTimeMillis()}.m4a")
        val fr = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK)
            .setAudioAttributes(AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_ASSISTANT).setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
            .build()
        am.requestAudioFocus(fr)
        focus = fr
        return try {
            val r = MediaRecorder(context)
            r.setAudioSource(MediaRecorder.AudioSource.MIC)
            r.setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
            r.setAudioEncoder(MediaRecorder.AudioEncoder.AAC)
            r.setAudioEncodingBitRate(48_000)
            r.setAudioSamplingRate(16_000)
            r.setAudioChannels(1)
            r.setOutputFile(f.absolutePath)
            builtInMic(am)?.let { r.setPreferredDevice(it) }
            r.prepare()
            r.start()
            recorder = r; file = f; startedAt = System.currentTimeMillis(); peak = 0
            true
        } catch (e: Exception) {
            releaseFocus(); f.delete(); false
        }
    }

    /** Пиковая громкость с прошлого вызова — чтобы понять, что звук реальный, а не тишина. */
    fun sampleAmplitude(): Int = (runCatching { recorder?.maxAmplitude }.getOrNull() ?: 0).also { if (it > peak) peak = it }

    /** @return файл и длительность, или null, если ничего не записалось. */
    fun stop(): Pair<File, Long>? {
        val r = recorder ?: return null
        sampleAmplitude()
        val duration = System.currentTimeMillis() - startedAt
        val ok = runCatching { r.stop() }.isSuccess
        r.release()
        recorder = null
        releaseFocus()
        val f = file ?: return null
        file = null
        if (!ok || f.length() == 0L) { f.delete(); return null }
        return f to duration
    }

    fun cancel() {
        recorder?.let { runCatching { it.stop() }; it.release() }
        recorder = null
        file?.delete(); file = null
        releaseFocus()
    }

    val heardSound: Boolean get() = peak > 600

    private fun releaseFocus() {
        focus?.let { context.getSystemService(AudioManager::class.java).abandonAudioFocusRequest(it) }
        focus = null
    }

    private fun builtInMic(am: AudioManager): AudioDeviceInfo? =
        am.getDevices(AudioManager.GET_DEVICES_INPUTS).firstOrNull { it.type == AudioDeviceInfo.TYPE_BUILTIN_MIC }
}
