"""Read-only probe of the M-VAVE FM-1: identity + DX7 dump requests. Writes nothing to the unit."""
import time, mido, pathlib

from fm1 import MIDI_IN as IN, MIDI_OUT as OUT     # found by name; they changed from 'FM-1 Audio n' to 'FM-1 Midi n'
out_dir = pathlib.Path(__file__).parent / 'dumps'
out_dir.mkdir(exist_ok=True)

REQUESTS = {
    'identity':   [0x7E, 0x7F, 0x06, 0x01],   # Universal Device Inquiry
    'voice_ch1':  [0x43, 0x20, 0x00],         # DX7 single voice (VCED) dump request
    'bank_ch1':   [0x43, 0x20, 0x09],         # DX7 32-voice (VMEM) dump request
}

def collect(inp, secs):
    msgs, end = [], time.time() + secs
    while time.time() < end:
        for m in inp.iter_pending():
            msgs.append(m)
        time.sleep(0.005)
    return msgs

with mido.open_input(IN) as inp, mido.open_output(OUT) as out:
    passive = collect(inp, 1.5)
    print(f'passive traffic (1.5s): {len(passive)} msgs', {m.type for m in passive})
    for name, data in REQUESTS.items():
        out.send(mido.Message('sysex', data=data))
        got = [m for m in collect(inp, 3.0) if m.type == 'sysex']
        print(f'\n[{name}] -> {len(got)} sysex replies')
        for i, m in enumerate(got):
            raw = bytes(m.bytes())
            print(f'  len={len(raw)} head={raw[:12].hex(" ")} ... tail={raw[-4:].hex(" ")}')
            (out_dir / f'{name}_{i}.syx').write_bytes(raw)
            if name == 'voice_ch1' and len(raw) == 163:
                print('  voice name:', bytes(raw[6 + 145:6 + 155]).decode('ascii', 'replace'))
            if name == 'bank_ch1' and len(raw) == 4104:
                names = [bytes(raw[6 + v*128 + 118: 6 + v*128 + 128]).decode('ascii', 'replace') for v in range(32)]
                print('  bank voices:', names)
