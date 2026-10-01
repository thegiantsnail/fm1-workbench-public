"""Hardware test of the Android app's MIDI looper, FM-1 on the phone's USB-C, driven over Wi-Fi adb via the debug
control hook. Records a 2-bar pad figure into the MIDI looper, captures two replay cycles with the app's audio looper,
pulls the WAV and checks that both cycles contain the same hits (same voices, one loop length apart)."""
import os, subprocess, sys, time, wave, re
import numpy as np

ADB = (os.path.expandvars(r'%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe') if sys.platform == 'win32'
       else os.path.expanduser('~/Library/Android/sdk/platform-tools/adb') if sys.platform == 'darwin' else 'adb')
DEV = sys.argv[1] if len(sys.argv) > 1 else os.environ.get('FM1_ADB_DEVICE') or sys.exit('usage: ' + sys.argv[0] + ' <adb device, e.g. 192.0.2.10:5555> (or set FM1_ADB_DEVICE)')
OUT = os.path.join(os.path.dirname(__file__), '..', '..', 'dumps')


def adb(*a):
    return subprocess.run([ADB, '-s', DEV, *a], capture_output=True, text=True).stdout


def cmd(c):
    adb('shell', 'am', 'broadcast', '-a', 'com.fm1.workbench.DEBUG', '-p', 'com.fm1.workbench', '--es', 'cmd', f"'{c}'")


def log_last():
    lines = [l for l in adb('logcat', '-d', '-s', 'FM1CTL:*').splitlines() if 'FM1CTL' in l]
    return lines[-1].split('FM1CTL  : ')[-1] if lines else ''


adb('logcat', '-c')
for c in ('panic', 'looper_clear', 'audio_clear', 'bpm 112'):
    cmd(c)
time.sleep(0.5)
print('start:', log_last())

beat = 60 / 112
figure = [0, 2, 1, 2] * 2                       # kick, hat, snare, hat - two bars of quarter notes
cmd('looper_rec'); t0 = time.perf_counter()
for i, pad in enumerate(figure):
    while time.perf_counter() - t0 < i * beat:
        time.sleep(0.002)
    cmd(f'pad {pad}')
while time.perf_counter() - t0 < 8 * beat:
    time.sleep(0.002)
cmd('looper_rec')                               # close: snaps to 2 bars
time.sleep(0.4)
print('loop closed:', log_last())

cmd('audio_rec'); time.sleep(2 * 8 * beat + 0.3); cmd('audio_rec')   # two replay cycles, then close the audio loop
time.sleep(0.5)
cmd('audio_save phone_midi_loop'); time.sleep(0.8)
saved = re.search(r'saved (\S+)', adb('logcat', '-d', '-s', 'FM1CTL:*'))
for c in ('looper_stop', 'audio_stop'):
    cmd(c)
if not saved:
    sys.exit('no WAV saved: ' + log_last())
local = os.path.join(OUT, 'phone_midi_loop.wav')
adb('pull', saved.group(1), local)

w = wave.open(local); sr = w.getframerate()
a = np.frombuffer(w.readframes(w.getnframes()), '<i2').reshape(-1, 2).mean(1) / 32768
env = np.sqrt(np.convolve(a ** 2, np.ones(48) / 48, 'same'))
thr = max(env.max() * 10 ** (-35 / 20), 1e-4)
up = np.flatnonzero((env[1:] > thr) & (env[:-1] <= thr)) / sr
ons = [t for i, t in enumerate(up) if i == 0 or t - up[i - 1] > 0.15]
L = 8 * beat
print(f'\ncaptured {len(a) / sr:.2f} s, peak {20 * np.log10(np.abs(a).max() + 1e-9):.1f} dBFS, {len(ons)} hits (figure has 8 per cycle)')


def bright(t):
    s = int(t * sr); x = a[s:s + 2048]
    m = np.abs(np.fft.rfft(x * np.hanning(len(x)))); f = np.fft.rfftfreq(len(x), 1 / sr)
    return (f * m).sum() / (m.sum() + 1e-9)


pairs = []
for t in ons:
    match = [u for u in ons if abs(u - (t + L)) < 0.02]
    if match:
        pairs.append((t, match[0]))
print(f'hits that repeat exactly one loop ({L * 1000:.0f} ms) later: {len(pairs)}')
for t, u in pairs[:8]:
    print(f'  {t:6.3f}s -> {u:6.3f}s  (gap {(u - t - L) * 1000:+.1f} ms)  brightness {bright(t):5.0f} / {bright(u):5.0f} Hz')
