package com.fm1.workbench.midi

import android.content.Context
import android.media.midi.MidiDevice
import android.media.midi.MidiDeviceInfo
import android.media.midi.MidiInputPort
import android.media.midi.MidiManager
import android.media.midi.MidiOutputPort
import android.media.midi.MidiReceiver
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import android.os.Process
import android.util.Log
import com.fm1.workbench.core.Engine
import java.util.concurrent.locks.LockSupport

/**
 * USB MIDI connection to the FM-1 (android.media.midi) + the engine clock thread.
 * - finds the device by name ("FM-1"), reconnects on hot-plug
 * - [engine] sends go straight to the FM-1's input port; a high-priority thread calls Engine.tick() every ~1 ms
 * - notes played on the FM-1's own keys are reported through [onKey] (the looper records them)
 */
private const val TAG = "FM1"

class Fm1Midi(context: Context, private val onStatus: (String?) -> Unit, private val onKey: (on: Boolean, note: Int, vel: Int) -> Unit) {
    private val mm = context.getSystemService(MidiManager::class.java)
    private val handlerThread = HandlerThread("fm1-midi").apply { start() }
    private val handler = Handler(handlerThread.looper)
    @Volatile private var device: MidiDevice? = null
    @Volatile private var inPort: MidiInputPort? = null
    @Volatile private var outPort: MidiOutputPort? = null
    @Volatile var deviceName: String? = null; private set

    val clock: () -> Double = { System.nanoTime() / 1e6 }
    val engine = Engine(clock) { bytes, at -> sendNow(bytes, at) }.apply { connected = false; sendAhead = 20.0 }

    /** Where the Engine's MIDI goes: AUTO = the FM-1 when it is connected, otherwise the Software FM-1. */
    enum class Output { AUTO, HARDWARE, SOFTWARE }
    @Volatile var output = Output.AUTO
        set(v) { field = v; updateRoute() }
    val soft by lazy { com.fm1.workbench.audio.SoftFm1Output(clock) }
    val usingSoft get() = output == Output.SOFTWARE || (output == Output.AUTO && inPort == null)
    private fun updateRoute() {
        engine.forgetDevice()
        engine.connected = usingSoft || inPort != null
        onStatus(if (usingSoft) "Software FM-1" else deviceName)
    }

    private val buf = ByteArray(1024)
    private fun sendNow(bytes: IntArray, atMs: Double) {
        if (usingSoft) { soft.send(bytes, atMs); return }
        // Timestamped: the MIDI service delivers at atMs (System.nanoTime base, same clock as the engine).
        val ts = if (atMs > clock()) (atMs * 1e6).toLong() else 0L
        val port = inPort ?: return
        // android.media.midi packets are limited in size; a 32-voice bank (4104 bytes) goes out in chunks.
        var i = 0
        while (i < bytes.size) {
            val n = minOf(512, bytes.size - i)
            for (k in 0 until n) buf[k] = bytes[i + k].toByte()
            try { if (ts > 0) port.send(buf, 0, n, ts) else port.send(buf, 0, n) } catch (_: Exception) { }
            i += n
        }
    }

    @Volatile private var running = true
    private val clockThread = Thread({
        Process.setThreadPriority(Process.THREAD_PRIORITY_URGENT_AUDIO)
        while (running) {
            engine.tick()
            LockSupport.parkNanos(1_000_000)          // ~1 ms dispatch granularity
        }
    }, "fm1-engine").apply { isDaemon = true; start() }

    private val callback = object : MidiManager.DeviceCallback() {
        override fun onDeviceAdded(info: MidiDeviceInfo) { if (isFm1(info)) open(info) }
        override fun onDeviceRemoved(info: MidiDeviceInfo) {
            if (device?.info?.id == info.id) close("FM-1 disconnected")
        }
    }

    init {
        mm.registerDeviceCallback(callback, handler)
        scan()
    }

    private fun allDevices(): Collection<MidiDeviceInfo> =
        if (Build.VERSION.SDK_INT >= 33) mm.getDevicesForTransport(MidiManager.TRANSPORT_MIDI_BYTE_STREAM)
        else @Suppress("DEPRECATION") mm.devices.toList()

    private fun label(info: MidiDeviceInfo): String {
        val p = info.properties
        return listOfNotNull(p.getString(MidiDeviceInfo.PROPERTY_NAME), p.getString(MidiDeviceInfo.PROPERTY_PRODUCT)).distinct().joinToString(" ")
    }

    private fun isFm1(info: MidiDeviceInfo) = label(info).contains("FM-1", ignoreCase = true) && info.inputPortCount > 0

    fun scan() {
        if (device != null) return
        val all = allDevices()
        Log.i(TAG, "scan: ${all.size} device(s): " + all.joinToString { "'${label(it)}' type=${it.type} in=${it.inputPortCount} out=${it.outputPortCount}" })
        val info = all.firstOrNull(::isFm1)
        if (info == null) updateRoute() else open(info)
    }

    fun availableDevices(): List<String> = allDevices().map(::label)

    @Volatile private var opening = false

    private fun open(info: MidiDeviceInfo) {
        // registerDeviceCallback also reports devices that are already present: don't open the FM-1 twice
        // (its input port can only be opened once, the second open would get null).
        synchronized(this) { if (device != null || opening) return; opening = true }
        Log.i(TAG, "opening '${label(info)}'")
        mm.openDevice(info, { d ->
            Log.i(TAG, "openDevice -> ${d != null}")
            opening = false
            if (d == null) { updateRoute(); return@openDevice }
            device = d
            inPort = d.openInputPort(0)                // the port we write TO the FM-1
            outPort = if (info.outputPortCount > 0) d.openOutputPort(0) else null
            outPort?.connect(object : MidiReceiver() {
                override fun onSend(msg: ByteArray, offset: Int, count: Int, timestamp: Long) = parseIncoming(msg, offset, count)
            })
            deviceName = label(info)
            Log.i(TAG, "connected: in=${inPort != null} out=${outPort != null}")
            updateRoute()
        }, handler)
    }

    private fun close(reason: String?) {
        engine.connected = false
        try { inPort?.close(); outPort?.close(); device?.close() } catch (_: Exception) { }
        inPort = null; outPort = null; device = null; deviceName = null
        updateRoute()
    }

    private var running_status = 0
    private fun parseIncoming(m: ByteArray, off: Int, count: Int) {
        var i = off
        val end = off + count
        while (i < end) {
            var b = m[i].toInt() and 0xFF
            if (b and 0x80 != 0) { running_status = b; i++; if (b >= 0xF0) continue } else b = running_status
            val type = b and 0xF0
            if ((type == 0x90 || type == 0x80) && i + 1 < end) {
                val note = m[i].toInt() and 0x7F; val vel = m[i + 1].toInt() and 0x7F
                onKey(type == 0x90 && vel > 0, note, vel)
                i += 2
            } else i++
        }
    }

    fun release() {
        running = false
        mm.unregisterDeviceCallback(callback)
        close(null)
        handlerThread.quitSafely()
    }
}
