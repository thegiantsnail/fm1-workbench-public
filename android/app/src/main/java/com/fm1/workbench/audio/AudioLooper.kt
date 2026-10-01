package com.fm1.workbench.audio

import android.annotation.SuppressLint
import android.content.Context
import android.media.AudioAttributes
import android.media.AudioDeviceInfo
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.AudioTrack
import android.media.MediaRecorder
import android.os.Process
import java.io.File
import java.io.RandomAccessFile
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.roundToInt

/**
 * Audio looper: records the FM-1's USB audio (the unit's own output, digitally) into the phone and loops it back
 * with overdub - captures effects and anything played on the unit. One audio thread reads the input and writes the
 * output in small blocks; overdub adds the input into the loop at (play position - [latencyMs]).
 *
 * Output goes to the phone (speaker/headphones) by default. Sending it to the FM-1's USB output while overdubbing
 * would re-record it: the FM-1 mixes USB playback back into its USB capture (measured, FINDINGS.md).
 */
class AudioLooper(private val context: Context) {
    enum class State { EMPTY, RECORDING, PLAYING, OVERDUBBING, STOPPED }

    @Volatile var state = State.EMPTY; private set
    @Volatile var level = 0f; private set             // input peak (0..1) for the meter
    @Volatile var latencyMs = 40                      // round-trip compensation for overdub
    @Volatile var bpm = 112
    @Volatile var syncBars = true
    @Volatile var inputName: String? = null; private set
    val sampleRate = 48000
    private val ch = 2
    private val maxFrames = sampleRate * 90                 // 90 s max loop
    private val loop = FloatArray(maxFrames * ch)
    private var undoBuf: FloatArray? = null
    @Volatile private var lengthFrames = 0
    @Volatile private var pos = 0
    private var recFrames = 0
    @Volatile private var thread: Thread? = null
    @Volatile private var run = false

    val lengthMs get() = lengthFrames * 1000.0 / sampleRate
    fun position(): Double = if (lengthFrames == 0) 0.0 else pos.toDouble() / lengthFrames
    val canUndo get() = undoBuf != null

    private fun usbInput(): AudioDeviceInfo? =
        context.getSystemService(AudioManager::class.java).getDevices(AudioManager.GET_DEVICES_INPUTS)
            .firstOrNull { it.type == AudioDeviceInfo.TYPE_USB_DEVICE || it.type == AudioDeviceInfo.TYPE_USB_HEADSET }

    /** Record button: EMPTY -> RECORDING -> PLAYING (loop closed) -> OVERDUBBING -> PLAYING. */
    @Synchronized fun record() {
        when (state) {
            State.EMPTY -> { recFrames = 0; pos = 0; lengthFrames = 0; state = State.RECORDING; ensureThread() }
            State.RECORDING -> closeLoop()
            State.PLAYING -> { undoBuf = loop.copyOf(lengthFrames * ch); state = State.OVERDUBBING }
            State.OVERDUBBING -> state = State.PLAYING
            State.STOPPED -> { undoBuf = loop.copyOf(lengthFrames * ch); pos = 0; state = State.OVERDUBBING; ensureThread() }
        }
    }

    private fun closeLoop() {
        var n = recFrames
        if (syncBars) {
            val bar = sampleRate * 4 * 60.0 / bpm
            n = (max(1.0, (recFrames / bar).roundToInt().toDouble()) * bar).toInt().coerceAtMost(maxFrames)
            // snapped longer than recorded: the tail is silence (already zero)
        }
        lengthFrames = n
        pos = if (recFrames >= n) recFrames - n else recFrames  // keep playing from "now"
        pos %= max(1, n)
        state = if (n > 0) State.PLAYING else State.EMPTY
    }

    @Synchronized fun play() { if (lengthFrames > 0 && state == State.STOPPED) { pos = 0; state = State.PLAYING; ensureThread() } }
    @Synchronized fun stop() { if (state == State.RECORDING) closeLoop(); if (lengthFrames > 0) state = State.STOPPED }
    @Synchronized fun undo() { undoBuf?.copyInto(loop); undoBuf = null }
    @Synchronized fun clear() { stop(); java.util.Arrays.fill(loop, 0f); lengthFrames = 0; undoBuf = null; state = State.EMPTY }

    @SuppressLint("MissingPermission")            // the UI requests RECORD_AUDIO before enabling this
    private fun ensureThread() {
        if (thread?.isAlive == true) return
        run = true
        thread = Thread({
            Process.setThreadPriority(Process.THREAD_PRIORITY_URGENT_AUDIO)
            val block = 256
            val inFmt = AudioFormat.Builder().setEncoding(AudioFormat.ENCODING_PCM_FLOAT).setSampleRate(sampleRate)
                .setChannelMask(AudioFormat.CHANNEL_IN_STEREO).build()
            val outFmt = AudioFormat.Builder().setEncoding(AudioFormat.ENCODING_PCM_FLOAT).setSampleRate(sampleRate)
                .setChannelMask(AudioFormat.CHANNEL_OUT_STEREO).build()
            val recMin = AudioRecord.getMinBufferSize(sampleRate, AudioFormat.CHANNEL_IN_STEREO, AudioFormat.ENCODING_PCM_FLOAT)
            val rec = AudioRecord.Builder().setAudioSource(MediaRecorder.AudioSource.UNPROCESSED).setAudioFormat(inFmt)
                .setBufferSizeInBytes(max(recMin, block * ch * 4 * 4)).build()
            usbInput()?.let { rec.setPreferredDevice(it); inputName = it.productName.toString() }
            val track = AudioTrack.Builder()
                .setAudioAttributes(AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA).setContentType(AudioAttributes.CONTENT_TYPE_MUSIC).build())
                .setAudioFormat(outFmt).setPerformanceMode(AudioTrack.PERFORMANCE_MODE_LOW_LATENCY)
                .setBufferSizeInBytes(block * ch * 4 * 4).build()
            val inBuf = FloatArray(block * ch); val outBuf = FloatArray(block * ch)
            rec.startRecording(); track.play()
            try {
                while (run) {
                    val got = rec.read(inBuf, 0, inBuf.size, AudioRecord.READ_BLOCKING)
                    if (got <= 0) continue
                    val frames = got / ch
                    var pk = 0f
                    for (i in 0 until got) pk = max(pk, abs(inBuf[i]))
                    level = pk
                    synchronized(this) {
                        when (state) {
                            State.RECORDING -> {
                                val room = minOf(frames, maxFrames - recFrames)
                                System.arraycopy(inBuf, 0, loop, recFrames * ch, room * ch)
                                recFrames += room
                                if (recFrames >= maxFrames) closeLoop()
                                java.util.Arrays.fill(outBuf, 0f)
                            }
                            State.PLAYING, State.OVERDUBBING -> {
                                val lat = (latencyMs * sampleRate / 1000.0).toInt()
                                for (f in 0 until frames) {
                                    val p = (pos + f) % lengthFrames
                                    outBuf[f * 2] = loop[p * 2]; outBuf[f * 2 + 1] = loop[p * 2 + 1]
                                    if (state == State.OVERDUBBING) {
                                        val w = ((p - lat) % lengthFrames + lengthFrames) % lengthFrames
                                        loop[w * 2] += inBuf[f * 2]; loop[w * 2 + 1] += inBuf[f * 2 + 1]
                                    }
                                }
                                pos = (pos + frames) % lengthFrames
                            }
                            else -> java.util.Arrays.fill(outBuf, 0f)
                        }
                    }
                    track.write(outBuf, 0, frames * ch, AudioTrack.WRITE_BLOCKING)
                }
            } finally {
                rec.stop(); rec.release(); track.stop(); track.release()
            }
        }, "fm1-audio-looper").apply { start() }
    }

    fun release() { run = false; thread?.join(500) }

    /** Save the loop as a 16-bit WAV (for sharing / DAW import). */
    @Synchronized fun exportWav(file: File): File {
        val n = lengthFrames * ch
        RandomAccessFile(file, "rw").use { f ->
            f.setLength(0)
            val hdr = ByteBuffer.allocate(44).order(ByteOrder.LITTLE_ENDIAN)
            hdr.put("RIFF".toByteArray()).putInt(36 + n * 2).put("WAVEfmt ".toByteArray()).putInt(16).putShort(1).putShort(ch.toShort())
                .putInt(sampleRate).putInt(sampleRate * ch * 2).putShort((ch * 2).toShort()).putShort(16)
                .put("data".toByteArray()).putInt(n * 2)
            f.write(hdr.array())
            val out = ByteBuffer.allocate(n * 2).order(ByteOrder.LITTLE_ENDIAN)
            for (i in 0 until n) out.putShort((loop[i].coerceIn(-1f, 1f) * 32767).toInt().toShort())
            f.write(out.array())
        }
        return file
    }
}
