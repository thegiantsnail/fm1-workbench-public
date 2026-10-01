"""A small software stand-in for the FM-1 (DX7 engine), to develop speech offline without the hardware.

Not a faithful DX7 - just enough to judge FM speech designs, calibrated on FM-1 measurements (FINDINGS.md):
  - output level: 0.75 dB per OL step (measured: OL 99->80 = -14 dB, level macro -20 steps = -15.2 dB)
  - modulation index: 13 x linear level; with modulator OL 58 this gives sidebands 14-15 dB under the carrier, as
    measured on the unit for the /a/ design (770 Hz carrier, 660/880 Hz at -15/-14 dB)
  - release: R4 72 falls 40 dB in ~50 ms, R4 >= 85 in < 20 ms (measured)
  - operator feedback for noise, DX7 algorithm routing (edges from the web app's dx7.js)
Notes are rendered independently and summed (the FM-1 gives each note its own voice; overlapping notes blend).
"""
import numpy as np

SR = 22050
ALG_SRC = ['2>1,6>5,5>4,4>3|6', '2>1,6>5,5>4,4>3|2', '3>2,2>1,6>5,5>4|6', '3>2,2>1,6>5,5>4|6', '2>1,4>3,6>5|6', '2>1,4>3,6>5|6',
           '2>1,4>3,5>3,6>5|6', '2>1,4>3,5>3,6>5|4', '2>1,4>3,5>3,6>5|2', '3>2,2>1,5>4,6>4|3', '3>2,2>1,5>4,6>4|6', '2>1,4>3,5>3,6>3|2',
           '2>1,4>3,5>3,6>3|6', '2>1,4>3,5>4,6>4|6', '2>1,4>3,5>4,6>4|2', '2>1,3>1,4>3,5>1,6>5|6', '2>1,3>1,4>3,5>1,6>5|2',
           '2>1,3>1,4>1,5>4,6>5|3', '3>2,2>1,6>4,6>5|6', '3>1,3>2,5>4,6>4|3', '3>1,3>2,6>4,6>5|3', '2>1,6>3,6>4,6>5|6', '3>2,6>4,6>5|6',
           '6>3,6>4,6>5|6', '6>4,6>5|6', '3>2,5>4,6>4|6', '3>2,5>4,6>4|3', '2>1,5>4,4>3|5', '4>3,6>5|6', '5>4,4>3|5', '6>5|6', '|6']
ALGS = []
for s in ALG_SRC:
    e, fb = s.split('|')
    edges = [tuple(map(int, p.split('>'))) for p in e.split(',')] if e else []
    ALGS.append((edges, int(fb), [o for o in range(1, 7) if not any(f == o for f, _ in edges)]))


def amp(ol):
    return 0.0 if ol <= 0 else 10 ** (-(99 - ol) * 0.75 / 20)


def midi_hz(n):
    return 440 * 2 ** ((n - 69) / 12)


def env(n, attack_ms=3, release_start=None, release_ms=12):
    """Attack ramp, sustain, exponential release after release_start (samples)."""
    e = np.ones(n)
    a = max(1, int(attack_ms / 1000 * SR)); e[:a] = np.linspace(0, 1, a)
    if release_start is not None and release_start < n:
        k = np.arange(n - release_start)
        e[release_start:] *= np.exp(-k / (release_ms / 1000 * SR))
    return e


def release_ms(r4):
    """Time constant for DX7 release rate R4 (fit to the FM-1: R4 72 -> -40 dB in ~50 ms, >= 85 -> < 20 ms)."""
    return 11 if r4 <= 72 else (4 if r4 < 85 else 2)


def render_note(v, key, dur_ms, tail_ms=60, rng=None):
    """One FM-1 note: voice dict (dx7.init_voice structure), MIDI key, held for dur_ms."""
    edges, fbop, carriers = ALGS[v['global']['ALG']]
    f0 = midi_hz(key + v['global'].get('TRNP', 24) - 24)
    n = int((dur_ms + tail_ms) / 1000 * SR); rel = int(dur_ms / 1000 * SR)
    t = np.arange(n) / SR
    out_of = {}
    fb = v['global'].get('FB', 0)
    # evaluate operators modulators-first (DX7 numbering: higher ops modulate lower ones in these algorithms)
    for o in range(6, 0, -1):
        op = v['ops'][o]
        if op['OL'] <= 0:
            out_of[o] = np.zeros(n); continue
        ratio = 0.5 if op['FC'] == 0 else op['FC'] * (1 + op['FF'] / 100)
        phase = 2 * np.pi * ratio * f0 * t
        mod = sum((13 * out_of[m] for m, to in edges if to == o), np.zeros(n))
        e = env(n, release_start=rel, release_ms=release_ms(op.get('R4', 72)))
        if o == fbop and fb > 0:
            beta = np.pi * 2 ** (fb - 7) * 1.4
            y = np.zeros(n); p1 = p2 = 0.0
            for i in range(n):                         # DX7-style feedback: average of the last two outputs
                s = np.sin(phase[i] + mod[i] + beta * (p1 + p2) / 2)
                p2, p1 = p1, s; y[i] = s
            if fb >= 6 and rng is not None:            # at high feedback the real engine turns to noise
                y = y + 0.6 * rng.standard_normal(n) * (fb - 5) / 2
        else:
            y = np.sin(phase + mod)
        out_of[o] = y * amp(op['OL']) * e
    return sum(out_of[c] for c in carriers) / max(1, len(carriers))


def render(notes, total_ms=None, seed=0):
    """notes: [(voice, key, start_ms, dur_ms)] -> mono float array at SR."""
    rng = np.random.default_rng(seed)
    end = max(s + d for _, _, s, d in notes) + 200 if total_ms is None else total_ms
    out = np.zeros(int(end / 1000 * SR) + SR // 10)
    for v, key, start, dur in notes:
        y = render_note(v, key, dur, rng=rng)
        i = int(start / 1000 * SR); out[i:i + len(y)] += y[:len(out) - i]
    return out
