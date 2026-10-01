import time, mido, numpy as np, pathlib
from test_diffswap import load_bank, vmem_to_vced, send_diff, panic, env, onset, T
from fm1 import out_port, recording, SR
bank = load_bank('sysexFinal/0_Original_Yamaha/0_DX7/ROM1A.syx')
V = [vmem_to_vced(bank[i]) for i in (0, 10, 18, 27)]
names = [bytes(v[145:155]).decode().strip() for v in V]
def vced_msg(v): return mido.Message('sysex', data=[0x43, 0, 0, 1, 0x1B] + list(v) + [(-sum(v)) & 0x7F])
def fp(x):   # log-spectrum fingerprint of the first 120 ms
    m = np.abs(np.fft.rfft(x * np.hanning(len(x))))[:2000]
    m = np.log(np.add.reduceat(m, np.arange(0, 2000, 20)) + 1e-6); return (m - m.mean()) / (m.std() + 1e-9)
with out_port() as o, recording(seconds_max=40) as rec:
    t0 = T(); now = lambda: T() - t0; refs, hits = [], []
    time.sleep(0.2)
    for k, v in enumerate(V):                      # clean references via full VCED + long settle
        o.send(vced_msg(v)); time.sleep(0.8)
        refs.append(now()); o.send(mido.Message('note_on', note=60, velocity=100)); time.sleep(0.3); o.send(mido.Message('note_off', note=60)); time.sleep(1.2)
    cur = list(V[-1]); order = [0, 2, 1, 3, 3, 0, 1, 2, 2, 1, 0, 3, 1, 3, 0, 2]
    for k in order:                                # diff-swapped hits
        send_diff(o, cur, V[k]); cur = list(V[k])
        hits.append((k, now())); o.send(mido.Message('note_on', note=60, velocity=100)); time.sleep(0.3); o.send(mido.Message('note_off', note=60)); time.sleep(1.2)
    panic(o)
a = rec['audio']; e = env(a)
def grab(t):
    on = onset(e, t, t + 0.5); return a[int(on * SR):int((on + 0.12) * SR)]
R = [fp(grab(t)) for t in refs]
ok = 0
for k, t in hits:
    s = [float(np.dot(fp(grab(t)), r) / len(r)) for r in R]; g = int(np.argmax(s)); ok += g == k
    print(f'sent {names[k]:10s} -> sounds like {names[g]:10s} (similarity {s[g]:.2f}{"" if g == k else "  WRONG"})')
print(f'{ok}/{len(hits)} correct')
