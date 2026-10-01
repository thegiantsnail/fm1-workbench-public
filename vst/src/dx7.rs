//! DX7 voice layout as the FM-1 understands it (VCED order, operator 6 first).
//!
//! Mirrors the FM-1 workbench's `dx7.py`. The edit buffer is 156 values: 145 voice parameters,
//! 10 name characters and the operator on/off mask.

pub const OP_FIELD_COUNT: usize = 21;
pub const GLOBAL_COUNT: usize = 19;
/// Voice parameters a host can automate: 6 operators x 21 fields + 19 globals.
pub const VOICE_PARAMS: usize = 6 * OP_FIELD_COUNT + GLOBAL_COUNT;
pub const NAME_START: usize = 145;
pub const NAME_LEN: usize = 10;
pub const OP_MASK: usize = 155;
pub const EDIT_SIZE: usize = 156;
pub const TRANSPOSE: usize = 144;
/// The device transpose stays here; the plugin shifts note numbers instead (see `engine`).
pub const TRANSPOSE_NEUTRAL: u8 = 24;

/// Positions of operator fields within an operator's 21 values.
pub mod op {
    pub const R1: usize = 0;
    pub const R2: usize = 1;
    pub const R3: usize = 2;
    pub const R4: usize = 3;
    pub const L1: usize = 4;
    pub const L2: usize = 5;
    pub const L3: usize = 6;
    pub const L4: usize = 7;
    pub const RS: usize = 13;
    pub const KVS: usize = 15;
    pub const OL: usize = 16;
    pub const MODE: usize = 17;
    pub const FC: usize = 18;
    pub const FF: usize = 19;
    pub const DT: usize = 20;
}

/// Edit-buffer indices of the voice globals used by name elsewhere.
pub mod global {
    pub const PR1: usize = 126;
    pub const PL4: usize = 133;
    pub const ALG: usize = 134;
    pub const FB: usize = 135;
    pub const LFS: usize = 137;
    pub const LPMD: usize = 139;
    pub const LPMS: usize = 143;
}

pub type Voice = [u8; EDIT_SIZE];

pub struct Field {
    /// Short DX7 name, used in parameter ids (`op1_ol`, `alg`).
    pub key: &'static str,
    pub label: &'static str,
    pub max: u8,
    pub init: u8,
}

const fn field(key: &'static str, label: &'static str, max: u8, init: u8) -> Field {
    Field { key, label, max, init }
}

pub const OP_FIELDS: [Field; OP_FIELD_COUNT] = [
    field("r1", "EG Rate 1", 99, 99),
    field("r2", "EG Rate 2", 99, 99),
    field("r3", "EG Rate 3", 99, 99),
    field("r4", "EG Rate 4", 99, 99),
    field("l1", "EG Level 1", 99, 99),
    field("l2", "EG Level 2", 99, 99),
    field("l3", "EG Level 3", 99, 99),
    field("l4", "EG Level 4", 99, 0),
    field("bp", "KLS Break Point", 99, 39),
    field("ld", "KLS Left Depth", 99, 0),
    field("rd", "KLS Right Depth", 99, 0),
    field("lc", "KLS Left Curve", 3, 0),
    field("rc", "KLS Right Curve", 3, 0),
    field("rs", "Rate Scaling", 7, 0),
    field("ams", "Amp Mod Sens", 3, 0),
    field("kvs", "Velocity Sens", 7, 0),
    field("ol", "Level", 99, 0),
    field("mode", "Osc Mode", 1, 0),
    field("fc", "Freq Coarse", 31, 1),
    field("ff", "Freq Fine", 99, 0),
    field("dt", "Detune", 14, 7),
];

pub const GLOBAL_FIELDS: [Field; GLOBAL_COUNT] = [
    field("pr1", "Pitch EG Rate 1", 99, 99),
    field("pr2", "Pitch EG Rate 2", 99, 99),
    field("pr3", "Pitch EG Rate 3", 99, 99),
    field("pr4", "Pitch EG Rate 4", 99, 99),
    field("pl1", "Pitch EG Level 1", 99, 50),
    field("pl2", "Pitch EG Level 2", 99, 50),
    field("pl3", "Pitch EG Level 3", 99, 50),
    field("pl4", "Pitch EG Level 4", 99, 50),
    field("alg", "Algorithm", 31, 0),
    field("fb", "Feedback", 7, 0),
    field("oks", "Osc Key Sync", 1, 1),
    field("lfs", "LFO Speed", 99, 35),
    field("lfd", "LFO Delay", 99, 0),
    field("lpmd", "LFO Pitch Mod Depth", 99, 0),
    field("lamd", "LFO Amp Mod Depth", 99, 0),
    field("lfks", "LFO Key Sync", 1, 1),
    field("lfw", "LFO Wave", 5, 0),
    field("lpms", "Pitch Mod Sens", 7, 3),
    field("trnp", "Transpose", 48, 24),
];

/// VCED index of an operator field; `op` is 1..=6 as shown on a DX7.
pub const fn op_index(op: usize, field: usize) -> usize {
    (6 - op) * OP_FIELD_COUNT + field
}

pub const fn global_index(field: usize) -> usize {
    6 * OP_FIELD_COUNT + field
}

/// Field metadata for a voice parameter index (0..145).
pub fn field_of(index: usize) -> &'static Field {
    if index < 6 * OP_FIELD_COUNT {
        &OP_FIELDS[index % OP_FIELD_COUNT]
    } else {
        &GLOBAL_FIELDS[index - 6 * OP_FIELD_COUNT]
    }
}

/// Operator number (1..=6) a voice parameter belongs to, or `None` for a global.
pub fn op_of(index: usize) -> Option<usize> {
    (index < 6 * OP_FIELD_COUNT).then(|| 6 - index / OP_FIELD_COUNT)
}

/// The DX7 INIT VOICE with operator 1 audible, as a full edit buffer.
pub fn init_voice() -> [u8; EDIT_SIZE] {
    let mut voice = [0u8; EDIT_SIZE];
    for (index, slot) in voice.iter_mut().enumerate().take(VOICE_PARAMS) {
        *slot = field_of(index).init;
    }
    voice[op_index(1, 16)] = 99; // OP1 output level
    set_name(&mut voice, "INIT VOICE");
    voice[OP_MASK] = 63;
    voice
}

pub fn set_name(voice: &mut [u8; EDIT_SIZE], name: &str) {
    let mut chars = name.bytes().filter(|b| (32..127).contains(b));
    for slot in &mut voice[NAME_START..NAME_START + NAME_LEN] {
        *slot = chars.next().unwrap_or(b' ');
    }
}

pub fn name_of(voice: &Voice) -> String {
    String::from_utf8_lossy(&voice[NAME_START..NAME_START + NAME_LEN]).trim_end().to_string()
}

/// Force every parameter into its range (generated and imported voices may exceed it).
pub fn clamp(voice: &mut Voice) {
    for (index, value) in voice.iter_mut().enumerate().take(VOICE_PARAMS) {
        *value = (*value).min(field_of(index).max);
    }
    voice[OP_MASK] = 63;
}

/// A packed library voice as a full edit buffer.
pub fn from_packed(packed: &[u8; 128]) -> Voice {
    let mut voice = [0u8; EDIT_SIZE];
    voice[..155].copy_from_slice(&vmem_to_vced(packed));
    for c in &mut voice[NAME_START..NAME_START + NAME_LEN] {
        if !(32..127).contains(c) {
            *c = b' ';
        }
    }
    clamp(&mut voice);
    voice
}

/// Unpack one 128-byte packed bank voice (VMEM) into the 155 VCED values.
pub fn vmem_to_vced(packed: &[u8; 128]) -> [u8; 155] {
    let mut out = [0u8; 155];
    for op in 0..6 {
        let q = &packed[op * 17..(op + 1) * 17];
        let o = &mut out[op * OP_FIELD_COUNT..(op + 1) * OP_FIELD_COUNT];
        o[..11].copy_from_slice(&q[..11]);
        o[11] = q[11] & 3;
        o[12] = (q[11] >> 2) & 3;
        o[13] = q[12] & 7;
        o[14] = q[13] & 3;
        o[15] = (q[13] >> 2) & 7;
        o[16] = q[14];
        o[17] = q[15] & 1;
        o[18] = (q[15] >> 1) & 31;
        o[19] = q[16];
        o[20] = (q[12] >> 3) & 15;
    }
    out[126..134].copy_from_slice(&packed[102..110]);
    out[134] = packed[110] & 31;
    out[135] = packed[111] & 7;
    out[136] = (packed[111] >> 3) & 1;
    out[137..141].copy_from_slice(&packed[112..116]);
    out[141] = packed[116] & 1;
    out[142] = (packed[116] >> 1) & 7;
    out[143] = (packed[116] >> 4) & 7;
    out[144] = packed[117];
    out[145..155].copy_from_slice(&packed[118..128]);
    for value in &mut out {
        *value &= 0x7F;
    }
    out
}

/// DX7 single parameter change: `F0 43 1n gg pp dd F7`.
///
/// The FM-1 applies these with no added latency, whereas a full voice dump stalls it for up to
/// 450 ms, so every voice change is sent as a series of these.
pub fn param_change(channel: u8, index: usize, value: u8) -> [u8; 7] {
    [
        0xF0,
        0x43,
        0x10 | (channel & 0x0F),
        ((index >> 7) & 3) as u8,
        (index & 0x7F) as u8,
        value & 0x7F,
        0xF7,
    ]
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn indices_match_the_workbench_layout() {
        assert_eq!(op_index(6, 0), 0); // VCED stores operator 6 first
        assert_eq!(op_index(1, 16), 121); // OP1 output level
        assert_eq!(global_index(8), 134); // algorithm
        assert_eq!(global_index(18), TRANSPOSE);
        assert_eq!(VOICE_PARAMS, 145);
        assert_eq!(op_of(0), Some(6));
        assert_eq!(op_of(125), Some(1));
        assert_eq!(op_of(126), None);
        assert_eq!(field_of(121).key, "ol");
        assert_eq!(field_of(134).key, "alg");
    }

    #[test]
    fn init_voice_is_audible_and_named() {
        let voice = init_voice();
        assert_eq!(voice[op_index(1, 16)], 99);
        assert_eq!(voice[op_index(2, 16)], 0);
        assert_eq!(voice[TRANSPOSE], TRANSPOSE_NEUTRAL);
        assert_eq!(&voice[NAME_START..NAME_START + NAME_LEN], b"INIT VOICE");
        assert_eq!(voice[OP_MASK], 63);
        for (index, value) in voice.iter().enumerate().take(VOICE_PARAMS) {
            assert!(*value <= field_of(index).max);
        }
    }

    #[test]
    fn parameter_change_addresses_indices_above_127() {
        assert_eq!(param_change(0, 121, 99), [0xF0, 0x43, 0x10, 0, 121, 99, 0xF7]);
        assert_eq!(param_change(0, OP_MASK, 63), [0xF0, 0x43, 0x10, 1, 27, 63, 0xF7]);
        assert_eq!(param_change(3, 144, 24)[2], 0x13);
    }

    #[test]
    fn packed_voice_unpacks_bit_fields() {
        let mut packed = [0u8; 128];
        packed[11] = 0b0000_1110; // operator 6: right curve 3, left curve 2
        packed[12] = 0b0111_1101; // detune 15, rate scaling 5
        packed[13] = 0b0001_1110; // velocity sens 7, amp mod sens 2
        packed[15] = 0b0011_1111; // coarse 31, fixed mode
        packed[110] = 0xFF; // algorithm masks to 31
        packed[117] = 36; // transpose
        packed[118..128].copy_from_slice(b"E.PIANO 1 ");
        let v = vmem_to_vced(&packed);
        assert_eq!((v[11], v[12], v[13], v[14], v[15]), (2, 3, 5, 2, 7));
        assert_eq!((v[17], v[18], v[20]), (1, 31, 15));
        assert_eq!((v[134], v[144]), (31, 36));
        assert_eq!(&v[145..155], b"E.PIANO 1 ");
    }
}
