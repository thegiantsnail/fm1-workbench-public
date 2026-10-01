"""Minimal DX7 voice model: build VCED (single voice, 155 params) and VMEM (32-voice bank) SysEx."""

OP_FIELDS = ['R1', 'R2', 'R3', 'R4', 'L1', 'L2', 'L3', 'L4', 'BP', 'LD', 'RD', 'LC', 'RC',
             'RS', 'AMS', 'KVS', 'OL', 'MODE', 'FC', 'FF', 'DT']
GLOBAL_FIELDS = ['PR1', 'PR2', 'PR3', 'PR4', 'PL1', 'PL2', 'PL3', 'PL4', 'ALG', 'FB', 'OKS',
                 'LFS', 'LFD', 'LPMD', 'LAMD', 'LFKS', 'LFW', 'LPMS', 'TRNP']


def param_index(op=None, field=None):
    """VCED parameter number. op = 1..6 (UI numbering); VCED stores OP6 first."""
    if op is None:
        return 126 + GLOBAL_FIELDS.index(field)
    return (6 - op) * 21 + OP_FIELDS.index(field)


def init_op(**kw):
    d = dict(R1=99, R2=99, R3=99, R4=99, L1=99, L2=99, L3=99, L4=0, BP=39, LD=0, RD=0, LC=0, RC=0,
             RS=0, AMS=0, KVS=0, OL=0, MODE=0, FC=1, FF=0, DT=7)
    d.update(kw)
    return d


def init_voice(name='INIT VOICE', ops=None, **glob):
    g = dict(PR1=99, PR2=99, PR3=99, PR4=99, PL1=50, PL2=50, PL3=50, PL4=50, ALG=0, FB=0, OKS=1,
             LFS=35, LFD=0, LPMD=0, LAMD=0, LFKS=1, LFW=0, LPMS=3, TRNP=24)
    g.update(glob)
    ops = ops or {}
    return {'ops': {n: init_op(**ops.get(n, {})) for n in range(1, 7)}, 'global': g, 'name': name}


def vced_bytes(v):
    out = []
    for n in range(6, 0, -1):
        out += [v['ops'][n][f] for f in OP_FIELDS]
    out += [v['global'][f] for f in GLOBAL_FIELDS]
    out += list(v['name'].ljust(10)[:10].encode('ascii'))
    assert len(out) == 155
    return out


def checksum(data):
    return (-sum(data)) & 0x7F


def vced_sysex(v, ch=0):
    """Full 163-byte single-voice dump F0 43 0n 00 01 1B ... cs F7 (mido wants it without F0/F7)."""
    body = vced_bytes(v)
    return [0x43, 0x00 | ch, 0x00, 0x01, 0x1B] + body + [checksum(body)]


OP_MAX = dict(R1=99, R2=99, R3=99, R4=99, L1=99, L2=99, L3=99, L4=99, BP=99, LD=99, RD=99, LC=3, RC=3,
              RS=7, AMS=3, KVS=7, OL=99, MODE=1, FC=31, FF=99, DT=14)
G_MAX = dict(PR1=99, PR2=99, PR3=99, PR4=99, PL1=99, PL2=99, PL3=99, PL4=99, ALG=31, FB=7, OKS=1,
             LFS=99, LFD=99, LPMD=99, LAMD=99, LFKS=1, LFW=5, LPMS=7, TRNP=48)
# Algorithm carriers (1-based ops), DX7 algorithms 1..32 — used to find which operators set the output level.
CARRIERS = [[1, 3], [1, 3], [1, 4], [1, 4], [1, 3, 5], [1, 3, 5], [1, 3], [1, 3], [1, 3], [1, 4], [1, 4], [1, 3], [1, 3],
            [1, 3], [1, 3], [1], [1], [1], [1, 4, 5], [1, 2, 4], [1, 2, 4, 5], [1, 3, 4, 5], [1, 2, 4, 5], [1, 2, 3, 4, 5],
            [1, 2, 3, 4, 5], [1, 2, 4], [1, 2, 4], [1, 3, 6], [1, 2, 3, 5], [1, 2, 3, 6], [1, 2, 3, 4, 5], [1, 2, 3, 4, 5, 6]]


def param_name(i):
    """VCED index -> readable name: 'OP1.OL', 'ALG', 'NAME3', 'OPMASK'."""
    if i < 126:
        return f'OP{6 - i // 21}.{OP_FIELDS[i % 21]}'
    if i < 145:
        return GLOBAL_FIELDS[i - 126]
    return 'OPMASK' if i == 155 else f'NAME{i - 145}'


def param_lookup(name):
    """'OP1.OL' / 'op3.fc' / 'ALG' -> (index, max). Raises ValueError for anything else: an unchecked operator
    number would map to the wrong parameter (OP0 -> a global, OP7 -> a negative index)."""
    n = str(name).strip().upper()
    if n.startswith('OP') and '.' in n:
        op, f = n[2:].split('.', 1)
        if not op.isdigit() or not 1 <= int(op) <= 6:
            raise ValueError(f'{name!r}: operator must be OP1..OP6')
        if f not in OP_MAX:
            raise ValueError(f'{name!r}: unknown operator field; use one of {", ".join(OP_FIELDS)}')
        return param_index(int(op), f), OP_MAX[f]
    if n not in G_MAX:
        raise ValueError(f'{name!r}: unknown parameter; use OPn.<field> or one of {", ".join(GLOBAL_FIELDS)}')
    return param_index(None, n), G_MAX[n]


def vmem_to_vced(p):
    """Unpack one 128-byte packed bank voice into the 155 VCED parameters."""
    out = []
    for op in range(6):                     # OP6 first, same as VCED
        q = p[op * 17:(op + 1) * 17]
        out += list(q[0:11]) + [q[11] & 3, (q[11] >> 2) & 3, q[12] & 7, q[13] & 3, (q[13] >> 2) & 7, q[14],
                                q[15] & 1, (q[15] >> 1) & 31, q[16], (q[12] >> 3) & 15]
    out += list(p[102:110]) + [p[110] & 31, p[111] & 7, (p[111] >> 3) & 1] + list(p[112:116]) + \
        [p[116] & 1, (p[116] >> 1) & 7, (p[116] >> 4) & 7, p[117]] + list(p[118:128])
    return [x & 0x7F for x in out]


def vced_list(v):
    """init_voice() dict -> 155 VCED values."""
    return vced_bytes(v)
