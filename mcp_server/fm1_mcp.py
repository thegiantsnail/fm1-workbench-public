"""FM-1 MCP server: lets an MCP client (Claude) play, program and *listen to* an M-VAVE FM-1 over USB.

Design rules come from measurements on the hardware (see FINDINGS.md):
  - voices are changed with DX7 single-parameter SysEx diffs (0 ms latency); full VCED dumps stall the unit,
    so they are never used here
  - sounding notes keep their voice, so a diff may be sent right after the previous note-on
  - the FM-1 ignores All Notes Off / All Sound Off: panic sends explicit note-offs
  - there is no MIDI master volume; distortion with gain 0 + its Level knob works as an output trim
  - the FM-1 matches a note-off using the transpose in effect at note-OFF time (a TRNP change while a note is held
    leaves it hanging), so the device's TRNP stays at 24 and each voice's transpose shifts the note numbers instead
"""
import base64, functools, json, pathlib, sys, threading, time, wave

import anyio

import mido
import numpy as np
import sounddevice as sd

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import dx7  # noqa: E402

from mcp.server.mcpserver import MCPServer  # noqa: E402

SR = 44100
FX = {  # effect -> {param: CC} on the Effect channel
    'filter': {'on': 0, 'type': 1, 'cutoff': 2, 'q': 3},
    'reverb': {'on': 4, 'type': 5, 'decay': 6, 'mix': 7},
    'delay': {'on': 8, 'decay': 9, 'rate': 10, 'mix': 11},
    'distortion': {'on': 12, 'gain': 13, 'tone': 14, 'level': 15},
    'chorus': {'on': 16, 'freq': 17, 'depth': 18, 'mix': 19},
    'phaser': {'on': 20, 'freq': 21, 'depth': 22, 'mix': 23},
}
FX_MAX = {'type': 2, 'cutoff': 107, 'q': 10}
NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']


def note_num(n):
    """60, '60', 'C4', 'F#3', 'Bb2' -> MIDI note number (C4 = 60)."""
    if isinstance(n, int):
        return n
    s = str(n).strip()
    if s.lstrip('-').isdigit():
        return int(s)
    name, octv = (s[:2], s[2:]) if len(s) > 2 and s[1] in '#b' else (s[:1], s[1:])
    name = name.upper().replace('DB', 'C#').replace('EB', 'D#').replace('GB', 'F#').replace('AB', 'G#').replace('BB', 'A#')
    return NOTE_NAMES.index(name) + 12 * (int(octv) + 1)


class Device:
    """Owns the MIDI port and mirrors what the FM-1 edit buffer holds so voice changes send only diffs."""

    def __init__(self):
        self.port = None
        self.ch, self.fx_ch = 0, 1
        self.dev = None                 # list(156) mirror of the edit buffer, or None if unknown
        self.voice_name = None
        self.lock = threading.RLock()
        self.play_thread = None
        self.stop_flag = threading.Event()      # stop token of the current/most recent run (each run gets its own)
        self.held = {}                  # note as played -> key actually sent
        self.trn = 0                    # transpose of the loaded voice, applied to note numbers

    # ---- ports ----
    def out(self):
        with self.lock:
            if self.port is None or self.port.closed:
                name = next((n for n in mido.get_output_names() if 'FM-1' in n), None)
                if not name:
                    raise RuntimeError('FM-1 MIDI output not found — is it plugged in?')
                self.port = mido.open_output(name)
                self.dev = None
            return self.port

    def send(self, msg):
        with self.lock:                  # tools run in worker threads; keep each message whole and ordered
            try:
                self.out().send(msg)
            except Exception:
                self.port = None        # device re-enumerated (renamed ports seen): reopen once
                self.out().send(msg)

    # ---- voice ----
    def set_voice(self, vced, name=None, op_mask=63):
        want = list(vced[:155]) + [op_mask]
        self.trn = want[144] - 24
        want[144] = 24                  # device transpose stays neutral
        n = 0
        with self.lock:
            for i in list(range(145)) + [155] + list(range(145, 155)):
                if self.dev is not None and self.dev[i] == want[i]:
                    continue
                self.send(mido.Message('sysex', data=[0x43, 0x10 | self.ch, (i >> 7) & 3, i & 127, want[i] & 127]))
                n += 1
            self.dev = want
            self.voice_name = name or bytes(want[145:155]).decode('ascii', 'replace').strip()
        return n

    def set_param(self, i, v):
        if i == 144:
            self.trn = v - 24; return  # transpose = note shift
        with self.lock:
            self.send(mido.Message('sysex', data=[0x43, 0x10 | self.ch, (i >> 7) & 3, i & 127, v & 127]))
            if self.dev is not None:
                self.dev[i] = v

    # ---- notes ----
    def note_on(self, n, v):
        key = max(0, min(127, n + self.trn))
        self.send(mido.Message('note_on', channel=self.ch, note=key, velocity=max(1, min(127, v))))
        self.held[n] = key

    def note_off(self, n):
        key = self.held.pop(n, max(0, min(127, n + self.trn)))
        self.send(mido.Message('note_off', channel=self.ch, note=key))

    def panic(self):
        self.stop_flag.set()
        # The FM-1 plays every channel, so a note stuck by another app can be on any of them.
        for ch in range(16):
            for n in range(128):
                self.send(mido.Message('note_off', channel=ch, note=n))
            self.send(mido.Message('control_change', channel=ch, control=64, value=0))
        self.held.clear()

    def cc(self, ch, cc, v):
        self.send(mido.Message('control_change', channel=ch, control=cc, value=max(0, min(127, int(v)))))

    def play_events(self, events, stop=None):
        """events: dicts with t (ms), note, vel, dur (ms), optional vced. Runs until done or until stop is set."""
        stop = stop or self.new_run()
        q = []
        for e in events:
            q.append((e['t'], 1, e))
            q.append((e['t'] + e['dur'], 0, e))
        q.sort(key=lambda x: (x[0], x[1]))               # offs before ons at equal times
        t0 = time.perf_counter() + 0.05
        pending_voice = None
        for i, (t, kind, e) in enumerate(q):
            # Prepare the next differing voice as early as allowed: right after the previous note-on.
            if kind == 1 and e.get('vced') is not None and pending_voice is not e:
                self.set_voice(e['vced'], e.get('voice_name'))
            target = t0 + t / 1000
            while True:
                if stop.is_set():
                    for n in list(self.held):
                        self.note_off(n)
                    return False
                d = target - time.perf_counter()
                if d <= 0:
                    break
                # short sleeps near the target for timing; never more than 20 ms so a stop is noticed quickly
                time.sleep(min(d, 0.002) if d < 0.01 else min(d - 0.008, 0.02))
            if kind == 1:
                if e['note'] in self.held:
                    self.note_off(e['note'])
                self.note_on(e['note'], e['vel'])
                nxt = next((x for x in q[i + 1:] if x[1] == 1 and x[2].get('vced') is not None), None)
                if nxt and nxt[2] is not e:
                    self.set_voice(nxt[2]['vced'], nxt[2].get('voice_name'))
                    pending_voice = nxt[2]
            else:
                self.note_off(e['note'])
        return True

    def new_run(self):
        """Stop whatever is playing and hand out a fresh stop token for the next run."""
        self.stop_flag.set()
        if self.play_thread and self.play_thread.is_alive():
            self.play_thread.join(2)
        self.stop_flag = threading.Event()
        return self.stop_flag


class Audio:
    """Records the FM-1's USB audio (the unit's own output, digitally)."""

    @staticmethod
    def device():
        found = [(i, sd.query_hostapis(d['hostapi'])['name']) for i, d in enumerate(sd.query_devices())
                 if 'FM-1' in d['name'] and d['max_input_channels'] > 0]
        if not found:
            raise RuntimeError('FM-1 audio input not found')
        return next((i for i, api in found if api == 'MME'), found[0][0])      # MME on Windows, else Core Audio/ALSA

    @staticmethod
    def record_during(fn, seconds_max=30):
        chunks, limit = [], int(seconds_max * SR)

        def cb(indata, frames, t, status):
            if sum(len(c) for c in chunks) < limit:
                chunks.append(indata.copy())
        with sd.InputStream(samplerate=SR, channels=2, device=Audio.device(), dtype='float32', callback=cb):
            fn()
        a = np.concatenate(chunks) if chunks else np.zeros((0, 2), 'float32')
        return a[:limit]

    @staticmethod
    def analyse(x):
        m = x.mean(axis=1) if x.ndim == 2 else x
        rms = float(np.sqrt(np.mean(m ** 2)) + 1e-12)
        pk = float(np.abs(x).max() + 1e-12)
        out = {'rms_dbfs': round(20 * np.log10(rms), 1), 'peak_dbfs': round(20 * np.log10(pk), 1),
               'clipped_samples': int((np.abs(x) >= 0.999).sum())}
        if rms > 1e-5 and len(m) > 2048:
            w = m * np.hanning(len(m)); mag = np.abs(np.fft.rfft(w)); f = np.fft.rfftfreq(len(m), 1 / SR)
            mag[f < 30] = 0
            out['dominant_hz'] = round(float(f[np.argmax(mag)]), 1)
            out['brightness_centroid_hz'] = round(float((f * mag).sum() / mag.sum()), 0)
        return out


dev = Device()
_library = None


def library():
    global _library
    if _library is None:
        path = ROOT / 'app' / 'library' / 'index.json'
        if not path.is_file():
            raise FileNotFoundError(f'{path} not found: build a library from your own .syx banks with '
                                    'build_library.py (see "Voices" in README.md)')
        idx = json.loads(path.read_text())
        _library = [{'id': i, 'name': n.strip(), 'bank': idx['banks'][b], 'slot': s, 'tags': t, 'b64': v}
                    for i, (n, b, s, v, t) in enumerate(idx['voices'])]
    return _library


def find_voice(ref):
    """'#123' id, exact name, or unique-ish partial name -> library record."""
    lib = library()
    r = str(ref).strip()
    if r.startswith('#') and r[1:].isdigit():
        return lib[int(r[1:])]
    exact = [v for v in lib if v['name'].lower() == r.lower()]
    if exact:
        return exact[0]
    part = [v for v in lib if r.lower() in v['name'].lower()]
    if not part:
        raise ValueError(f'no voice matching {ref!r}; use fm1_search_voices')
    return part[0]


def vced_of(rec):
    return dx7.vmem_to_vced(base64.b64decode(rec['b64']))


def voice_summary(vced):
    alg = vced[134]
    carriers = dx7.CARRIERS[alg]
    ops = {}
    for op in range(1, 7):
        base = (6 - op) * 21
        fc, ff, mode = vced[base + 18], vced[base + 19], vced[base + 17]
        freq = (f'{10 ** (fc % 4) * 10 ** (ff / 100):.1f} Hz fixed' if mode else f'x{(0.5 if fc == 0 else fc) * (1 + ff / 100):.2f}')
        ops[f'OP{op}'] = {'role': 'carrier' if op in carriers else 'modulator', 'level': vced[base + 16], 'freq': freq,
                          'attack_rate': vced[base], 'decay_rate': vced[base + 1], 'sustain_level': vced[base + 6],
                          'release_rate': vced[base + 3], 'vel_sens': vced[base + 15]}
    return {'name': bytes(vced[145:155]).decode('ascii', 'replace').strip(), 'algorithm': alg + 1, 'feedback': vced[135],
            'transpose': vced[144] - 24, 'lfo': {'speed': vced[137], 'pitch_depth': vced[139], 'amp_depth': vced[140]},
            'operators': ops}


server = MCPServer('fm1', instructions=(
    'Controls an M-VAVE FM-1 (DX7-compatible 6-operator FM synth) over USB MIDI and can record its USB audio to '
    'verify results. Voices come from a 35k-voice DX7 library (fm1_search_voices). The FM-1 is single-timbral; '
    'fm1_play_sequence can still switch voices per note. Use fm1_measure/fm1_meter to hear what the unit outputs.'))


def blocking_tool(fn):
    """Register a tool that sleeps/records/plays in a worker thread, so the server stays responsive (fm1_stop,
    fm1_status) while it runs. Returns the plain function so other tools can still call it synchronously."""
    @functools.wraps(fn)
    async def run(*args, **kwargs):
        return await anyio.to_thread.run_sync(functools.partial(fn, *args, **kwargs))
    server.tool()(run)
    return fn


@blocking_tool
def fm1_status() -> dict:
    """Ports, current voice, and a 0.3 s audio level reading from the FM-1."""
    out = {'midi_out': next((n for n in mido.get_output_names() if 'FM-1' in n), None),
           'midi_in': next((n for n in mido.get_input_names() if 'FM-1' in n), None),
           'key_channel': dev.ch + 1, 'fx_channel': dev.fx_ch + 1, 'voice': dev.voice_name,
           'voice_known': dev.dev is not None, 'library_size': len(library())}
    try:
        out['audio_now'] = Audio.analyse(Audio.record_during(lambda: time.sleep(0.3)))
    except Exception as e:
        out['audio_error'] = str(e)
    return out


@server.tool()
def fm1_search_voices(query: str = '', category: str = '', limit: int = 20) -> list:
    """Search the DX7 library by name/bank substring and/or category tag (kick, snare, hat, tom, perc, bass, keys,
    organ, brass, strings, pad, bell, lead, pluck, wind, fx; or 'drums' for any percussion). Returns ids like '#123'."""
    q, c = query.lower(), category.lower()
    drums = {'kick', 'snare', 'hat', 'tom', 'perc'}
    hits = [v for v in library() if (not q or q in v['name'].lower() or q in v['bank'].lower())
            and (not c or (c == 'drums' and drums & set(v['tags'])) or c in v['tags'])]
    return [{'id': f"#{v['id']}", 'name': v['name'], 'bank': v['bank'], 'tags': v['tags']} for v in hits[:max(1, min(limit, 200))]] \
        + ([{'more': len(hits) - limit}] if len(hits) > limit else [])


@blocking_tool
def fm1_load_voice(voice: str, audition: bool = True) -> dict:
    """Load a library voice ('#id' or name) into the FM-1 (via parameter diffs) and optionally play a test note."""
    rec = find_voice(voice)
    vced = vced_of(rec)
    n = dev.set_voice(vced, rec['name'])
    if audition:
        time.sleep(0.02); dev.note_on(60, 100); time.sleep(0.5); dev.note_off(60)
    return {'loaded': rec['name'], 'id': f"#{rec['id']}", 'bank': rec['bank'], 'params_sent': n, **voice_summary(vced)}


@server.tool()
def fm1_get_voice(full: bool = False) -> dict:
    """Describe the voice currently in the FM-1 (as sent by this server). full=True returns every parameter by name."""
    if dev.dev is None:
        return {'error': 'voice unknown — load one with fm1_load_voice or fm1_init_voice first'}
    out = voice_summary(dev.dev[:144] + [dev.trn + 24] + dev.dev[145:])
    if full:
        out['params'] = {dx7.param_name(i): (dev.trn + 24 if i == 144 else dev.dev[i]) for i in range(145)}
    return out


@server.tool()
def fm1_init_voice(preset: str = 'sine') -> dict:
    """Start from a simple voice: 'sine' (1 carrier), 'init' (DX7 INIT VOICE), or 'epiano' (2-op bell/EP starter)."""
    if preset == 'sine':
        v = dx7.init_voice('SINE', ALG=31, ops={1: dict(OL=99, KVS=2)})
    elif preset == 'epiano':
        v = dx7.init_voice('SIMPLE EP', ALG=4, ops={1: dict(OL=99, R2=40, L3=0, R4=60, KVS=3), 2: dict(OL=72, FC=14, R2=70, L3=0, KVS=4),
                                                    3: dict(OL=97, R2=38, L3=0, R4=60), 4: dict(OL=65, FC=1, R2=45, L3=0)})
    else:
        v = dx7.init_voice('INIT VOICE', ops={1: dict(OL=99)})
    vced = dx7.vced_list(v)
    dev.dev = None
    dev.set_voice(vced, v['name'])
    return voice_summary(vced)


@server.tool()
def fm1_set_params(params: dict) -> dict:
    """Change voice parameters live. Keys: 'ALG' (1-32), 'FB' (0-7), 'TRNP', 'LFS', 'LPMD', 'PL1'.. or per operator
    'OP1.OL' (level 0-99), 'OP1.FC' (coarse 0-31), 'OP1.FF' (fine), 'OP1.DT' (detune 0-14, 7=center), 'OP1.R1'..'R4'
    (envelope rates), 'OP1.L1'..'L4' (levels), 'OP1.KVS' (velocity sens 0-7), 'OP1.MODE' (0 ratio/1 fixed). Applies to
    the next note played."""
    # Validate everything first (raises on unknown names / OP0 / OP7), so a bad key doesn't leave a half-applied edit.
    plan = []
    for k, v in params.items():
        idx, mx = dx7.param_lookup(k)
        is_alg = k.strip().upper() == 'ALG'
        plan.append((idx, max(0, min(mx, int(v) - 1 if is_alg else int(v))), is_alg))
    if dev.dev is None:
        fm1_init_voice('init')
    done = {}
    for idx, val, is_alg in plan:
        dev.set_param(idx, val)
        done[dx7.param_name(idx)] = val + 1 if is_alg else val
    return {'set': done}


@blocking_tool
def fm1_play_note(note: str = 'C4', velocity: int = 100, duration_ms: int = 500) -> str:
    """Play one note (number or name like 'A3', 'F#4') on the current voice."""
    n = note_num(note)
    dev.note_on(n, velocity); time.sleep(duration_ms / 1000); dev.note_off(n)
    return f'played {note} ({n}) vel {velocity} for {duration_ms} ms'


@blocking_tool
def fm1_play_sequence(events: list, bpm: float = 0, background: bool = False) -> dict:
    """Play timed notes, optionally switching voices per note (multi-voice on a single-voice synth).
    events: [{"note": "C3"|48, "start": 0, "dur": 250, "vel": 100, "voice": "#123"|"name"(optional)}].
    start/dur are ms, or 16th-note steps when bpm > 0. Sounding notes keep their voice when the next voice loads.
    background=True returns immediately (stop with fm1_stop)."""
    step = 60000 / bpm / 4 if bpm else 1
    cache, ev = {}, []
    for e in events:
        vced = name = None
        if e.get('voice'):
            if e['voice'] not in cache:
                rec = find_voice(e['voice']); cache[e['voice']] = (vced_of(rec), rec['name'])
            vced, name = cache[e['voice']]
        ev.append({'t': float(e.get('start', 0)) * step, 'dur': max(5.0, float(e.get('dur', 1 if bpm else 250)) * step),
                   'note': note_num(e.get('note', 60)), 'vel': int(e.get('vel', 100)), 'vced': vced, 'voice_name': name})
    length = max((x['t'] + x['dur'] for x in ev), default=0)
    if background:
        token = dev.new_run()                     # stops and joins the previous run first
        dev.play_thread = threading.Thread(target=dev.play_events, args=(ev, token), daemon=True)
        dev.play_thread.start()
        return {'playing': len(ev), 'length_ms': round(length)}
    if length > 60000:
        raise ValueError('over 60 s — use background=True')
    done = dev.play_events(ev)
    return {'played': len(ev), 'length_ms': round(length), 'completed': done, 'voice_now': dev.voice_name}


@server.tool()
def fm1_stop() -> str:
    """Stop a background sequence and release its notes."""
    dev.stop_flag.set()
    return 'stopped'


@blocking_tool
def fm1_panic() -> str:
    """Release every note (explicit note-offs — the FM-1 ignores All Notes Off) and lift sustain."""
    dev.panic()
    return 'all 128 notes released, sustain off'


@server.tool()
def fm1_set_fx(effect: str, on: bool = True, params: dict | None = None) -> dict:
    """Effects on the FX channel. effect: filter|reverb|delay|distortion|chorus|phaser.
    params (0-100 unless noted): filter type (0 LP,1 BP,2 HP), cutoff (0-107), q (0-10); reverb type (0-2), decay, mix;
    delay decay, rate, mix; distortion gain, tone, level; chorus/phaser freq, depth, mix."""
    e = FX[effect.lower()]
    sent = {'on': int(on)}
    dev.cc(dev.fx_ch, e['on'], 1 if on else 0)
    for k, v in (params or {}).items():
        mx = FX_MAX.get(k, 100)
        dev.cc(dev.fx_ch, e[k.lower()], max(0, min(mx, int(v))))
        sent[k] = int(v)
    return {effect: sent}


@server.tool()
def fm1_output_trim(level: int = 50) -> dict:
    """Turn the FM-1's output down from the computer (it has no MIDI master volume). Uses distortion with gain 0 as a
    clean gain stage: level 100 ~ -3 dB, 50 ~ -8 dB, 20 ~ -14 dB. level=-1 switches the trim off (distortion off).
    Note: this occupies the distortion effect."""
    if level < 0:
        dev.cc(dev.fx_ch, 12, 0)
        return {'trim': 'off'}
    for cc, v in ((13, 0), (15, max(0, min(100, level))), (12, 1)):
        dev.cc(dev.fx_ch, cc, v)
    return {'trim_level': level}


@server.tool()
def fm1_program_change(preset: int) -> str:
    """Select onboard preset 1-128 (the server then no longer knows the voice contents)."""
    dev.send(mido.Message('program_change', channel=dev.ch, program=max(1, min(128, preset)) - 1))
    dev.dev = None; dev.voice_name = f'onboard preset {preset}'
    time.sleep(0.06)                     # the unit is busy ~45 ms after a program change
    return f'preset {preset} selected'


@blocking_tool
def fm1_measure(note: str = 'A4', velocity: int = 100, duration_ms: int = 600) -> dict:
    """Play a note and measure the FM-1's actual USB audio: RMS/peak dBFS, dominant frequency, brightness, clipping,
    plus how long the tail rings after release."""
    n = note_num(note)

    def go():
        time.sleep(0.15); dev.note_on(n, velocity); time.sleep(duration_ms / 1000); dev.note_off(n); time.sleep(0.6)
    x = Audio.record_during(go)
    m = x.mean(axis=1)
    body = x[int(0.2 * SR):int((0.15 + duration_ms / 1000) * SR)]
    env = np.sqrt(np.convolve(m ** 2, np.ones(441) / 441, 'same'))
    rel = int((0.15 + duration_ms / 1000) * SR)
    after = np.flatnonzero(env[rel:] > 10 ** (-70 / 20))
    res = Audio.analyse(body)
    res['tail_ms'] = round(len(after) and (after[-1] / SR * 1000), 0)
    res['before_dbfs'] = Audio.analyse(x[:int(0.12 * SR)])['rms_dbfs']
    res['note'] = n
    return res


@blocking_tool
def fm1_meter(seconds: float = 5.0) -> dict:
    """Just listen: record the FM-1's USB audio for a few seconds (you play) and report level per 0.5 s."""
    seconds = max(0.5, min(60.0, seconds))
    x = Audio.record_during(lambda: time.sleep(seconds), seconds + 1)
    h = int(0.5 * SR)
    per = [Audio.analyse(x[i:i + h]) for i in range(0, len(x) - h + 1, h)]
    return {'overall': Audio.analyse(x), 'per_half_second': [{'t': i * 0.5, 'rms': p['rms_dbfs'], 'peak': p['peak_dbfs']} for i, p in enumerate(per)]}


@blocking_tool
def fm1_record(seconds: float = 5.0, name: str = 'take', play: list | None = None, bpm: float = 0) -> dict:
    """Record the FM-1 to dumps/recordings/<name>.wav, optionally while playing a sequence (same format as
    fm1_play_sequence events)."""
    seconds = max(0.5, min(120.0, seconds))

    def go():
        t0 = time.perf_counter()
        if play:
            fm1_play_sequence(play, bpm=bpm)
        rest = seconds - (time.perf_counter() - t0)
        if rest > 0:
            time.sleep(rest)
    x = Audio.record_during(go, seconds + 70)
    out = ROOT / 'dumps' / 'recordings'; out.mkdir(parents=True, exist_ok=True)
    path = out / f"{''.join(c for c in name if c.isalnum() or c in '-_') or 'take'}.wav"
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype('<i2').tobytes())
    return {'file': str(path), 'seconds': round(len(x) / SR, 2), **Audio.analyse(x)}


@server.tool()
def fm1_export_voice(name: str = '') -> dict:
    """Save the current voice as a DX7 single-voice .syx in dumps/voices/ (loadable by Dexed, the Workbench, etc.)."""
    if dev.dev is None:
        return {'error': 'voice unknown'}
    body = dev.dev[:155]
    data = bytes([0xF0, 0x43, 0x00, 0x00, 0x01, 0x1B] + body + [(-sum(body)) & 0x7F, 0xF7])
    out = ROOT / 'dumps' / 'voices'; out.mkdir(parents=True, exist_ok=True)
    fn = (name or dev.voice_name or 'voice').replace(' ', '_')
    path = out / f"{''.join(c for c in fn if c.isalnum() or c in '-_')}.syx"
    path.write_bytes(data)
    return {'file': str(path), 'bytes': len(data)}


if __name__ == '__main__':
    server.run('stdio')
