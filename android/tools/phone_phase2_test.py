"""Phase-2 hardware tests on the phone (FM-1 on USB-C, Wi-Fi adb, debug control hook):
 A) MIDI player: demo.mid with a library voice per channel + drum kit on ch10, captured by the app's audio looper
 B) screen-off playback: drum sequencer + audio looper keep running with the screen off (foreground service)"""
import os, re, subprocess, sys, time, wave
import numpy as np

ADB = (os.path.expandvars(r'%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe') if sys.platform == 'win32'
       else os.path.expanduser('~/Library/Android/sdk/platform-tools/adb') if sys.platform == 'darwin' else 'adb')
DEV = sys.argv[1] if len(sys.argv) > 1 else os.environ.get('FM1_ADB_DEVICE') or sys.exit('usage: ' + sys.argv[0] + ' <adb device, e.g. 192.0.2.10:5555> (or set FM1_ADB_DEVICE)')
DUMPS = os.path.join(os.path.dirname(__file__), '..', '..', 'dumps')


def adb(*a):
    return subprocess.run([ADB, '-s', DEV, *a], capture_output=True, text=True).stdout


def cmd(c, wait=0.15):
    adb('shell', 'am', 'broadcast', '-a', 'com.fm1.workbench.DEBUG', '-p', 'com.fm1.workbench', '--es', 'cmd', f"'{c}'")
    time.sleep(wait)


def last(pattern=''):
    lines = [l.split('FM1CTL  : ')[-1] for l in adb('logcat', '-d', '-s', 'FM1CTL:*').splitlines() if 'FM1CTL' in l and pattern in l]
    return lines[-1] if lines else ''


def save_and_pull(name):
    cmd(f'audio_save {name}', 0.8)
    m = re.search(r'saved (\S+)', last('saved'))
    local = os.path.join(DUMPS, name + '.wav')
    adb('pull', m.group(1), local)
    w = wave.open(local); sr = w.getframerate()
    return np.frombuffer(w.readframes(w.getnframes()), '<i2').reshape(-1, 2).mean(1) / 32768, sr


def profile(a, sr):
    h = sr // 2
    return [round(float(20 * np.log10(np.sqrt(np.mean(a[i:i + h] ** 2)) + 1e-9))) for i in range(0, len(a) - h + 1, h)]


def onsets(a, sr, rel_db=-35, gap=0.06):
    env = np.sqrt(np.convolve(a ** 2, np.ones(48) / 48, 'same'))
    thr = max(env.max() * 10 ** (rel_db / 20), 1e-4)
    up = np.flatnonzero((env[1:] > thr) & (env[:-1] <= thr)) / sr
    return [t for i, t in enumerate(up) if i == 0 or t - up[i - 1] > gap], env


adb('logcat', '-c')
for c in ('panic', 'looper_clear', 'audio_clear', 'bpm 112'):
    cmd(c)

# ---------------- A) player ----------------
print('A) MIDI player')
cmd('player_demo', 0.5)
for ch, voice in ((1, 'E.PIANO 1'), (2, 'BRASS   1'), (3, 'HARPSICH 1')):
    cmd(f'player_voice {ch} {voice}')
    print(f'   ch{ch} -> {voice}:', last('player_voice')[:60])
cmd('audio_rec'); cmd('player_play'); time.sleep(8.5); cmd('audio_rec'); cmd('player_stop', 0.4)
print('   state:', last())
a, sr = save_and_pull('phone_player')
print('   loudness per 0.5 s:', profile(a, sr))
ons, env = onsets(a, sr)
print(f'   captured {len(a) / sr:.1f} s, peak {20 * np.log10(np.abs(a).max() + 1e-9):.1f} dBFS, {len(ons)} onsets '
      f'(demo: 16th-note drums + 8th bass at 104 BPM -> ~7 events/s)')
cmd('audio_clear')

# ---------------- B) screen off ----------------
print('B) screen-off playback')
cmd('seq_play'); cmd('audio_rec'); time.sleep(1.5)
adb('shell', 'input', 'keyevent', 'KEYCODE_SLEEP'); t_off = time.time()
time.sleep(1.0)
screen = adb('shell', 'dumpsys', 'power')
awake = re.search(r'mWakefulness=(\w+)', screen).group(1)
svc = adb('shell', 'dumpsys', 'activity', 'services', 'com.fm1.workbench')
fg = re.search(r'isForeground=(\w+)', svc)
types = re.search(r'foregroundServiceType=(0x[0-9a-f]+)', svc)
print(f'   screen: {awake}; PlaybackService foreground={fg.group(1) if fg else "not running"} types={types.group(1) if types else "?"}')
time.sleep(5.0)
adb('shell', 'input', 'keyevent', 'KEYCODE_WAKEUP'); t_on = time.time()
time.sleep(1.0)
cmd('audio_rec'); cmd('seq_stop', 0.4)
print('   state:', last())
a, sr = save_and_pull('phone_screen_off')
print('   loudness per 0.5 s:', profile(a, sr), ' (-180 = silenced)')
ons, env = onsets(a, sr, rel_db=-40, gap=0.05)
sd = 60 / 112 / 4
dur = len(a) / sr
# hits per second across the capture, and grid consistency
per_s = [sum(1 for t in ons if s <= t < s + 1) for s in range(int(dur))]
if not ons: sys.exit('no hits captured')
t0 = ons[0]
err = np.array([((t - t0) / sd - round((t - t0) / sd)) * sd * 1000 for t in ons])
print(f'   captured {dur:.1f} s ({t_on - t_off:.1f} s of it with the screen off, starting ~{1.5:.1f} s in)')
print(f'   hits per second: {per_s}')
print(f'   on the 16th grid: {np.mean(np.abs(err) < 12) * 100:.0f}% within 12 ms (median |err| {np.median(np.abs(err)):.1f} ms)')
cmd('audio_clear'); cmd('panic')
