"""Controlled probes: software FM-1 vs the unit, one mechanism at a time (sustained A4 = 440 Hz, velocity 100).
  resp   single sine carrier at ratio 1..31: output level vs frequency
  fb     operator 6 alone with feedback 0..7: harmonic levels H2..H6 relative to H1
  index  operator 2 (ratio 1 or 14) modulating operator 1 at OL 40..99: first sidebands relative to the carrier

    python test_soft_probe.py [resp] [fb] [index]
"""
import json, sys
import numpy as np
import dx7
from test_soft_parity import hardware, software, onset, SR, OUT

NOTE = 69
F0 = 440.0


def voice(alg=0, fb=0, ops=None):
    v = dx7.init_voice('PROBE', ALG=alg, FB=fb, OKS=1, TRNP=24, LPMD=0, LAMD=0)
    for n in range(1, 7):
        v['ops'][n].update(R1=99, R2=99, R3=99, R4=99, L1=99, L2=99, L3=99, L4=0, OL=0, FC=1, FF=0, DT=7, KVS=0, RS=0,
                           LD=0, RD=0, MODE=0)
    for n, kw in (ops or {}).items(): v['ops'][n].update(kw)
    d = dx7.vced_list(v)[:155]
    return [0xF0, 0x43, 0x00, 0x00, 0x01, 0x1B, *d, (128 - (sum(d) & 127)) & 127, 0xF7]


def spectrum(x):
    seg = x[int(0.15 * SR):int(0.75 * SR)]
    s = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
    f = np.fft.rfftfreq(len(seg), 1 / SR)
    return lambda hz: 20 * np.log10(s[(f > hz * 0.97) & (f < hz * 1.03)].max() + 1e-12)


def measure(sx, tag):
    import test_soft_parity as P
    P.NOTE = NOTE
    hw, sw = hardware(sx), software(sx, tag)
    return spectrum(hw[onset(hw):]), spectrum(sw[onset(sw):])


def main(which):
    res = {}
    if 'resp' in which:
        rows = []
        for fc in (1, 2, 4, 6, 8, 12, 16, 20, 24, 28, 31):
            h, s = measure(voice(ops={1: dict(OL=99, FC=fc)}), f'resp{fc}')
            rows.append((fc, F0 * fc, h(F0 * fc), s(F0 * fc)))
        ref = rows[0]
        print('response (dB re ratio 1):  Hz     fm1   soft')
        for fc, hz, a, b in rows: print(f'  x{fc:<3} {hz:7.0f} {a - ref[2]:6.1f} {b - ref[3]:6.1f}')
        res['resp'] = rows
    if 'fb' in which:
        rows = []
        print('feedback (H2..H6 dB re H1):         fm1                       soft')
        for fb in range(8):
            h, s = measure(voice(alg=31, fb=fb, ops={6: dict(OL=99)}), f'fb{fb}')
            hh = [h(F0 * k) - h(F0) for k in range(2, 7)]; ss = [s(F0 * k) - s(F0) for k in range(2, 7)]
            rows.append((fb, hh, ss))
            print(f'  FB{fb}  ' + ' '.join(f'{x:6.1f}' for x in hh) + '   |' + ' '.join(f'{x:6.1f}' for x in ss))
        res['fb'] = rows
    if 'index' in which:
        rows = []
        print('index (first sideband dB re carrier):  fm1   soft')
        for fc in (1, 14):
            for ol in (40, 50, 58, 66, 74, 82, 90, 99):
                h, s = measure(voice(alg=0, ops={1: dict(OL=99), 2: dict(OL=ol, FC=fc)}), f'idx{fc}_{ol}')
                sb = F0 * (1 + fc)                              # upper first sideband
                rows.append((fc, ol, h(sb) - h(F0), s(sb) - s(F0)))
                print(f'  ratio {fc:2} OL {ol:2}   {rows[-1][2]:6.1f} {rows[-1][3]:6.1f}')
        res['index'] = rows
    if 'fbol' in which:                                   # does feedback depth follow the operator's output level?
        rows = []
        print('feedback 7 vs operator level (H2, H3 dB re H1):   fm1          soft')
        for alg, lab in ((31, 'carrier'), (30, 'modulator->op5')):
            for ol in (99, 90, 82, 74):
                ops = {6: dict(OL=ol)} if alg == 31 else {6: dict(OL=ol), 5: dict(OL=99)}
                h, s = measure(voice(alg=alg, fb=7, ops=ops), f'fbol{alg}_{ol}')
                hh = [h(F0 * k) - h(F0) for k in (2, 3)]; ss = [s(F0 * k) - s(F0) for k in (2, 3)]
                rows.append((lab, ol, hh, ss))
                print(f'  {lab:15} OL {ol}   {hh[0]:6.1f} {hh[1]:6.1f}   | {ss[0]:6.1f} {ss[1]:6.1f}')
        res['fbol'] = rows
    if 'vel' in which:                                    # velocity curve: carrier level and modulator depth (KVS 7)
        import test_soft_parity as P
        rows = []
        print('velocity (KVS 7):  carrier level dB re vel 127 | modulator sideband dB re carrier   fm1 / soft')
        for vel in (20, 50, 80, 100, 110, 127):
            P.VEL = vel
            h1, s1 = measure(voice(ops={1: dict(OL=99, KVS=7)}), f'velc{vel}')
            h2, s2 = measure(voice(ops={1: dict(OL=99), 2: dict(OL=80, KVS=7)}), f'velm{vel}')
            rows.append((vel, h1(F0), s1(F0), h2(2 * F0) - h2(F0), s2(2 * F0) - s2(F0)))
        P.VEL = 100
        for vel, a, b, c, d in rows:
            print(f'  vel {vel:3}   {a - rows[-1][1]:6.1f} / {b - rows[-1][2]:6.1f}   |   {c:6.1f} / {d:6.1f}')
        res['vel'] = rows
    if 'fbmod' in which:                                  # feedback operator as a gentle modulator (DX7 alg 31: 6 -> 5)
        rows = []
        print('op6 (feedback) -> op5, op6 OL 50/58: H2 H3 H4 dB re H1        fm1            ||      soft')
        for fb in (0, 4, 6, 7):
            for ol in (50, 58):
                h, s = measure(voice(alg=30, fb=fb, ops={6: dict(OL=ol), 5: dict(OL=99)}), f'fbm{fb}_{ol}')
                hh = [h(F0 * k) - h(F0) for k in (2, 3, 4)]; ss = [s(F0 * k) - s(F0) for k in (2, 3, 4)]
                rows.append((fb, ol, hh, ss))
                print(f'  FB {fb} OL {ol}  ' + ' '.join(f'{x:6.1f}' for x in hh) + '  || ' + ' '.join(f'{x:6.1f}' for x in ss), flush=True)
        res['fbmod'] = rows
    if 'rs' in which:                                     # keyboard rate scaling: decay R2 = 40 from L1 99 to L2 50
        import test_soft_parity as P
        from scipy.signal import hilbert
        rows = []
        print('rate scaling: ms for the decay to fall 20 dB (R2 40, L2 50)       fm1 / soft')
        for note in (48, 60, 72, 84):
            P.NOTE = note
            for rs in (0, 4, 7):
                sx = voice(ops={1: dict(OL=99, R2=40, L2=50, L3=50, RS=rs)})
                hw, sw = hardware(sx), software(sx, f'rs{note}_{rs}')
                def t20(x):
                    k = int(0.005 * SR); x = x[onset(x):]; n = len(x) // k
                    e = 10 * np.log10(np.array([np.mean(x[i * k:(i + 1) * k] ** 2) for i in range(n)]) + 1e-20)
                    top = e[:10].max(); return int(np.argmax(e > -1e9) + np.argmax(e[2:] < top - 20)) * 5
                rows.append((note, rs, t20(hw), t20(sw)))
                print(f'  note {note} RS {rs}   {rows[-1][2]:6d} / {rows[-1][3]:6d}', flush=True)
        P.NOTE = NOTE
        res['rs'] = rows
    if 'fbeg' in which:                                   # feedback 7, lowered by envelope level instead of OL
        rows = []
        print('FB 7 carrier, OL 99, envelope levels L1-L3 = x: H2 H3 dB re H1      fm1  ||  soft')
        for lv in (99, 90, 82, 74):
            h, s = measure(voice(alg=31, fb=7, ops={6: dict(OL=99, L1=lv, L2=lv, L3=lv)}), f'fbeg{lv}')
            hh = [h(F0 * k) - h(F0) for k in (2, 3)]; ss = [s(F0 * k) - s(F0) for k in (2, 3)]
            rows.append((lv, hh, ss))
            print(f'  L {lv}   {hh[0]:6.1f} {hh[1]:6.1f}  || {ss[0]:6.1f} {ss[1]:6.1f}', flush=True)
        res['fbeg'] = rows
    if 'alg22' in which:                                  # DX7 alg 22: op6 -> op3, op4, op5 (+ op2 -> op1)
        rows = []
        print('alg 22: op6 at OL 50/70 modulating one carrier at a time: first sideband dB re carrier   fm1 / soft')
        for car in (3, 4, 5, 1):
            for ol in (50, 70):
                ops = {6: dict(OL=ol), car: dict(OL=99)}
                if car == 1: ops = {2: dict(OL=ol), 1: dict(OL=99)}
                h, s = measure(voice(alg=21, ops=ops), f'a22_{car}_{ol}')
                rows.append((car, ol, h(2 * F0) - h(F0), s(2 * F0) - s(F0), h(F0), s(F0)))
                print(f'  carrier op{car} ({"op2" if car == 1 else "op6"} OL {ol})   {rows[-1][2]:6.1f} / {rows[-1][3]:6.1f}', flush=True)
        # all three carriers at once, as Brass 1 and the speech voices use them
        for ol in (50, 70):
            h, s = measure(voice(alg=21, ops={6: dict(OL=ol), 3: dict(OL=99), 4: dict(OL=99), 5: dict(OL=99)}), f'a22_all_{ol}')
            print(f'  carriers 3+4+5 (op6 OL {ol})   {h(2 * F0) - h(F0):6.1f} / {s(2 * F0) - s(F0):6.1f}    level vs single op3: '
                  f'{h(F0) - rows[0 if ol == 50 else 1][4]:+5.1f} / {s(F0) - rows[0 if ol == 50 else 1][5]:+5.1f} dB', flush=True)
        res['alg22'] = rows
    if 'velmod' in which:                                 # modulator velocity, low index (sideband tracks depth 1:1)
        import test_soft_parity as P
        rows = []
        print('modulator velocity (OL 56, sideband dB re carrier):  KVS 7 fm1 / soft   |   KVS 3 fm1 / soft')
        for vel in (20, 50, 80, 100, 110, 127):
            P.VEL = vel
            r = [vel]
            for kvs in (7, 3):
                h, s = measure(voice(ops={1: dict(OL=99), 2: dict(OL=56, KVS=kvs)}), f'vm{kvs}_{vel}')
                r += [h(2 * F0) - h(F0), s(2 * F0) - s(F0)]
            rows.append(r)
            print(f'  vel {vel:3}   {r[1]:6.1f} / {r[2]:6.1f}   |   {r[3]:6.1f} / {r[4]:6.1f}')
        P.VEL = 100
        res['velmod'] = rows
    if 'lfo' in which or 'attack' in which:
        import test_soft_parity as P
        from scipy.signal import hilbert
        P.HOLD_MS = 2500
        def track(x):
            seg = x[int(0.3 * SR):int(2.3 * SR)]
            a = hilbert(seg)
            env = 20 * np.log10(np.abs(a) + 1e-9)
            inst = np.diff(np.unwrap(np.angle(a))) * SR / (2 * np.pi)
            k = int(0.01 * SR)
            env = np.convolve(env, np.ones(k) / k, 'valid')[::k]; inst = np.convolve(inst, np.ones(k) / k, 'valid')[::k]
            cents = 1200 * np.log2(np.clip(inst, 1, None) / F0)
            def rate(y):
                y = y - y.mean(); s = np.abs(np.fft.rfft(y * np.hanning(len(y)))); f = np.fft.rfftfreq(len(y), 0.01)
                return f[1 + np.argmax(s[1:])]
            return (np.percentile(env, 95) - np.percentile(env, 5), rate(env), (np.percentile(cents, 95) - np.percentile(cents, 5)) / 2, rate(cents))
        if 'lfo' in which:
            rows = []
            print('LFO (sine): AM p-p dB / AM Hz / vibrato ±cents / vibrato Hz       fm1  ||  soft')
            cases = [(f'speed {s}', dict(LFS=s, LPMD=60, LPMS=5)) for s in (10, 35, 60, 85)] + \
                    [(f'PMS {p}', dict(LFS=35, LPMD=99, LPMS=p)) for p in (1, 3, 5, 7)] + \
                    [(f'AMS {a}', dict(LFS=35, LAMD=99, ams=a)) for a in (1, 2, 3)] + [('AMS 3 AMD 50', dict(LFS=35, LAMD=50, ams=3))]
            for lab, g in cases:
                ams = g.pop('ams', 0)
                v = dx7.init_voice('PROBE', ALG=0, FB=0, OKS=1, TRNP=24, LFD=0, LFKS=1, LFW=4, LPMD=g.get('LPMD', 0), LAMD=g.get('LAMD', 0), LFS=g['LFS'], LPMS=g.get('LPMS', 0))
                for n in range(1, 7): v['ops'][n].update(R1=99, R2=99, R3=99, R4=99, L1=99, L2=99, L3=99, L4=0, OL=0, FC=1, FF=0, DT=7, KVS=0, RS=0, LD=0, RD=0, MODE=0, AMS=0)
                v['ops'][1].update(OL=99, AMS=ams)
                d = dx7.vced_list(v)[:155]; sx = [0xF0, 0x43, 0, 0, 1, 0x1B, *d, (128 - (sum(d) & 127)) & 127, 0xF7]
                hw, sw = hardware(sx), software(sx, 'lfo_' + lab.replace(' ', ''))
                a, b = track(hw[onset(hw):]), track(sw[onset(sw):])
                rows.append((lab, a, b))
                print(f'  {lab:13} ' + ' '.join(f'{x:6.1f}' for x in a) + '  || ' + ' '.join(f'{x:6.1f}' for x in b), flush=True)
            res['lfo'] = rows
        if 'attack' in which:
            rows = []
            print('attack (R1): ms to -3 dB of the sustain      fm1 / soft')
            for r1 in (20, 35, 50, 65, 80):
                sx = voice(ops={1: dict(OL=99, R1=r1)})
                hw, sw = hardware(sx), software(sx, f'att{r1}')
                def t3(x):
                    k = int(0.005 * SR); n = len(x) // k
                    e = 10 * np.log10(np.array([np.mean(x[i * k:(i + 1) * k] ** 2) for i in range(n)]) + 1e-20)
                    on = int(np.argmax(e > e.max() - 30))
                    sus = np.median(e[on + int(1.6 / 0.005):on + int(2.2 / 0.005)])
                    return int(np.argmax(e[on:] > sus - 3)) * 5
                rows.append((r1, t3(hw), t3(sw)))
                print(f'  R1 {r1:2}   {rows[-1][1]:7.0f} / {rows[-1][2]:7.0f}', flush=True)
            res['attack'] = rows
        P.HOLD_MS = 1000
    prev = json.loads((OUT / 'probe.json').read_text()) if (OUT / 'probe.json').exists() else {}
    (OUT / 'probe.json').write_text(json.dumps({**prev, **res}, default=float))


if __name__ == '__main__':
    main(sys.argv[1:] or ['resp', 'fb', 'index'])
