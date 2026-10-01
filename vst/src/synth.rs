//! The built-in synth: a software FM-1, so the plugin can be heard without the hardware.
//!
//! This is a port of the workbench's Software FM-1 (`app/fm1-synth.js`: `FM1T`, `FM1Fx`,
//! `FM1Core`), kept line for line so that a test can render the same messages through both and
//! compare the samples. It is fed the very messages the plugin sends to the unit: notes,
//! single-parameter SysEx and controllers. Like the FM-1, a parameter change only affects the
//! next note: each note takes a snapshot of the edit buffer.
//!
//! The constants were fitted to measurements of the hardware by the workbench (its FINDINGS.md):
//! 0.75 dB per output-level step, feedback strength, the output's treble roll-off, pitch envelope
//! speed, LFO depths. The effects are standard designs with ranges picked to feel alike; the
//! unit's own algorithms are unpublished. Not emulated: the unit's stored presets, pitch bend and
//! the mod wheel. Voice dumps are not understood either; the plugin never sends them.
//!
//! Realtime-safe once built: `queue` and `render` neither allocate nor wait.

use std::collections::VecDeque;
use std::f64::consts::PI;
use std::sync::OnceLock;

use crate::algo;
use crate::dx7::{self, EDIT_SIZE, OP_MASK};
use crate::engine::Msg;

const SIN_N: usize = 4096;
pub const MAX_VOICES: usize = 16;
const QUEUE: usize = 4096;
/// Fitted to the unit: feedback strength, and the gentle treble roll-off of its output.
const FB_SCALE: f64 = 0.25;
const OUT_LP_HZ: f64 = 5600.0;
/// Stock firmware's LFO ignores the voice's speed, waveform and delay: always about this, a sine.
const LFO_HZ: f64 = 5.8;
/// Output volume, set so that the synth is as loud as the FM-1's own USB audio at the unit's
/// full volume. Measured on eight library voices (FM-1+VA 093): at the workbench's default of
/// 0.8 the synth was 4.9 dB louder (4.3 to 5.5).
const VOLUME: f64 = 0.456;
/// The unit's output also rolls off the bass: a first-order high-pass at 20 Hz, measured with
/// the init voice against this synth (-0.4 dB at 65 Hz, -1.4 at 33, -3.9 at 16, -8.4 at 8).
/// The workbench's synth has no such filter, and it needs one: a feedback operator that
/// modulates a carrier at the same pitch, a few cents apart, makes an offset that wanders at
/// their beat rate, far below hearing (up to -5 dB of the signal on a library lead).
const OUT_HP_HZ: f64 = 20.0;
/// After this long without a voice or a sound above `QUIET`, rendering is skipped. It is longer
/// than the longest effect memory (the delay line, about a second).
const IDLE_SECONDS: f64 = 2.0;
const QUIET: f32 = 1e-6;

const LOW: [i32; 20] = [0, 5, 9, 13, 17, 20, 23, 25, 27, 29, 31, 33, 35, 37, 39, 41, 42, 43, 45, 46];
const VEL: [i32; 64] = [
    0, 70, 86, 97, 106, 114, 121, 126, 132, 138, 142, 148, 152, 156, 160, 163, 166, 170, 173, 174,
    178, 181, 184, 186, 189, 190, 194, 196, 198, 200, 202, 205, 206, 209, 211, 214, 216, 218, 220,
    222, 224, 225, 227, 229, 230, 232, 233, 235, 237, 238, 240, 241, 242, 243, 244, 246, 246, 248,
    249, 250, 251, 252, 253, 254,
];
const EXPSCALE: [i32; 33] = [
    0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 14, 16, 19, 23, 27, 33, 39, 47, 56, 66, 80, 94, 110, 126,
    142, 158, 174, 190, 206, 222, 238, 250,
];
/// Pitch envelope level 0..99 in 1/32 octave.
const PLV: [i32; 100] = [
    -128, -116, -104, -95, -85, -76, -68, -61, -56, -52, -49, -46, -43, -41, -39, -37, -35, -33,
    -32, -31, -30, -29, -28, -27, -26, -25, -24, -23, -22, -21, -20, -19, -18, -17, -16, -15, -14,
    -13, -12, -11, -10, -9, -8, -7, -6, -5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11,
    12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35,
    38, 40, 43, 46, 49, 53, 58, 65, 73, 82, 92, 103, 115, 127,
];
const PRATE: [i32; 100] = [
    1, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21,
    22, 23, 24, 25, 26, 27, 28, 30, 31, 33, 34, 36, 37, 38, 39, 41, 42, 44, 46, 47, 49, 51, 53, 54,
    56, 58, 60, 62, 64, 66, 68, 70, 72, 74, 76, 79, 82, 85, 88, 91, 94, 98, 102, 106, 110, 115,
    120, 125, 130, 135, 141, 147, 153, 159, 165, 171, 178, 185, 193, 202, 211, 232, 243, 254, 255,
    255, 255, 255, 255, 255, 255, 255,
];
/// Vibrato depth by pitch-modulation sensitivity, before scaling to semitones at full depth.
const PMS: [f64; 8] = [0.0, 10.0, 20.0, 33.0, 55.0, 92.0, 153.0, 255.0];
/// Tremolo depth by amplitude-modulation sensitivity, in dB peak to peak at full depth.
const AMS_DB: [f64; 4] = [0.0, 2.1, 5.3, 130.0];
/// FM-1+VA LFO speed in Hz, measured through CC 76 at these speed values; between them the
/// speed is interpolated on a logarithmic scale.
const LFO_SPEEDS: [(f64, f64); 5] = [(0.0, 0.06), (31.0, 5.0), (62.0, 10.4), (86.0, 33.7), (99.0, 50.7)];
/// Effect controllers as the unit's panel has them at power-on, every effect off.
const FX_DEFAULTS: [(usize, u8); 17] = [
    (2, 80), (3, 2), (5, 1), (6, 50), (7, 30), (9, 40), (10, 40), (11, 30), (13, 30), (14, 50),
    (15, 50), (17, 30), (18, 50), (19, 40), (21, 30), (22, 50), (23, 40),
];

fn scale_out(level: u8) -> i32 {
    if level >= 20 {
        28 + level as i32
    } else {
        LOW[level as usize]
    }
}

fn pms(sensitivity: u8) -> f64 {
    PMS[sensitivity.min(7) as usize] / 255.0 * 12.0 * 0.88
}

fn ams(sensitivity: u8) -> f64 {
    AMS_DB[sensitivity.min(3) as usize] * 256.0 / 6.02
}

fn lfo_hz(speed: u8) -> f64 {
    let speed = speed as f64;
    for pair in LFO_SPEEDS.windows(2) {
        let ((a, fa), (b, fb)) = (pair[0], pair[1]);
        if speed <= b {
            return fa * (fb / fa).powf((speed - a) / (b - a));
        }
    }
    50.7
}

/// One algorithm: for each operator the operators that modulate it, and those that are heard.
#[derive(Clone, Copy, Default)]
struct Algorithm {
    mods: [[u8; 6]; 7],
    mod_count: [u8; 7],
    carriers: [u8; 6],
    carrier_count: u8,
    feedback: u8,
}

struct Tables {
    /// One cycle plus two guard points for interpolation. Stored in single precision, as the
    /// workbench stores them, so both renders round alike.
    sin: Vec<f32>,
    /// Level units (256 per doubling, 3840 = full) to amplitude.
    exp: Vec<f32>,
    algorithms: [Algorithm; 32],
}

fn tables() -> &'static Tables {
    static TABLES: OnceLock<Tables> = OnceLock::new();
    TABLES.get_or_init(|| {
        let sin = (0..SIN_N + 2).map(|i| (2.0 * PI * i as f64 / SIN_N as f64).sin() as f32).collect();
        let exp = (0..=4096).map(|i| 2f64.powf((i as f64 - 3840.0) / 256.0) as f32).collect();
        let mut algorithms = [Algorithm::default(); 32];
        for (number, algorithm) in algorithms.iter_mut().enumerate() {
            for (from, to) in algo::edges(number as u8) {
                let to = to as usize;
                algorithm.mods[to][algorithm.mod_count[to] as usize] = from;
                algorithm.mod_count[to] += 1;
            }
            for (index, _) in algo::carriers(number as u8).iter().enumerate().filter(|(_, &c)| c) {
                algorithm.carriers[algorithm.carrier_count as usize] = index as u8 + 1;
                algorithm.carrier_count += 1;
            }
            algorithm.feedback = algo::feedback_op(number as u8);
        }
        Tables { sin, exp, algorithms }
    })
}

struct Comb {
    buffer: Vec<f32>,
    at: usize,
    damped: f64,
}

struct Allpass {
    buffer: Vec<f32>,
    at: usize,
}

/// The FM-1's effect section, driven by CC 0..23 on the effect channel as the hardware is:
/// 0-3 filter on/type/cutoff/Q, 4-7 reverb on/type/decay/mix, 8-11 delay on/decay/rate/mix,
/// 12-15 distortion on/gain/tone/level, 16-19 chorus on/freq/depth/mix, 20-23 phaser
/// on/freq/depth/mix. Chain: distortion, filter, chorus, phaser, delay, reverb.
struct Fx {
    sr: f64,
    cc: [u8; 24],
    svf: [f64; 2],
    tone_z: f64,
    delay: Vec<f32>,
    delay_w: usize,
    chorus: Vec<f32>,
    chorus_w: usize,
    chorus_phase: f64,
    phaser: [f32; 4],
    phaser_a: f64,
    phaser_phase: f64,
    phaser_fb: f64,
    phaser_n: u32,
    combs: [Comb; 4],
    allpasses: [Allpass; 2],
    // Derived from `cc` by `update`.
    dist_on: bool,
    drive: f64,
    tone_a: f64,
    level: f64,
    filter_on: bool,
    filter_type: u8,
    g: f64,
    k: f64,
    reverb_on: bool,
    reverb_len: [usize; 4],
    reverb_fb: f64,
    reverb_damp: f64,
    reverb_mix: f64,
    delay_on: bool,
    delay_fb: f64,
    delay_len: usize,
    delay_mix: f64,
    chorus_on: bool,
    chorus_rate: f64,
    chorus_depth: f64,
    chorus_mix: f64,
    phaser_on: bool,
    phaser_rate: f64,
    phaser_depth: f64,
    phaser_mix: f64,
}

impl Fx {
    fn new(sr: f64) -> Self {
        let line = |seconds: f64| vec![0f32; (seconds * sr).ceil() as usize + 2];
        let comb = |n: f64| Comb { buffer: vec![0f32; (n * sr / 44100.0 * 1.25).round() as usize], at: 0, damped: 0.0 };
        let allpass = |n: f64| Allpass { buffer: vec![0f32; (n * sr / 44100.0).round() as usize], at: 0 };
        let mut cc = [0u8; 24];
        for (index, value) in FX_DEFAULTS {
            cc[index] = value;
        }
        let mut fx = Fx {
            sr,
            cc,
            svf: [0.0; 2],
            tone_z: 0.0,
            delay: line(1.05),
            delay_w: 0,
            chorus: line(0.04),
            chorus_w: 0,
            chorus_phase: 0.0,
            phaser: [0.0; 4],
            phaser_a: 0.0,
            phaser_phase: 0.0,
            phaser_fb: 0.0,
            phaser_n: 0,
            combs: [comb(1116.0), comb(1188.0), comb(1277.0), comb(1356.0)],
            allpasses: [allpass(556.0), allpass(441.0)],
            dist_on: false,
            drive: 1.0,
            tone_a: 0.0,
            level: 1.0,
            filter_on: false,
            filter_type: 0,
            g: 0.0,
            k: 0.0,
            reverb_on: false,
            reverb_len: [1; 4],
            reverb_fb: 0.0,
            reverb_damp: 0.0,
            reverb_mix: 0.0,
            delay_on: false,
            delay_fb: 0.0,
            delay_len: 1,
            delay_mix: 0.0,
            chorus_on: false,
            chorus_rate: 0.0,
            chorus_depth: 0.0,
            chorus_mix: 0.0,
            phaser_on: false,
            phaser_rate: 0.0,
            phaser_depth: 0.0,
            phaser_mix: 0.0,
        };
        fx.update();
        fx
    }

    fn set(&mut self, controller: u8, value: u8) {
        if let Some(slot) = self.cc.get_mut(controller as usize) {
            *slot = value;
            self.update();
        }
    }

    fn update(&mut self) {
        let (c, sr) = (self.cc.map(|v| v as f64), self.sr);
        self.dist_on = c[12] > 0.0;
        self.drive = 10f64.powf(c[13] * 0.36 / 20.0);
        self.tone_a = 1.0 - (-2.0 * PI * 800.0 * 15f64.powf(c[14] / 100.0) / sr).exp();
        self.level = 10f64.powf((c[15] - 50.0) * 0.47 / 20.0);
        self.filter_on = c[0] > 0.0;
        self.filter_type = self.cc[1].min(2);
        self.g = (PI * (sr * 0.45).min(20.0 * 2f64.powf(c[2].min(107.0) / 107.0 * 10.0)) / sr).tan();
        self.k = 1.0 / (0.5 + c[3].min(10.0) * 1.2);
        self.reverb_on = c[4] > 0.0;
        let kind = self.cc[5].min(2) as usize;
        let size = [0.55, 1.0, 0.8][kind];
        for (len, comb) in self.reverb_len.iter_mut().zip(&self.combs) {
            *len = ((comb.buffer.len() as f64 * size).floor() as usize).max(1);
        }
        self.reverb_fb = 0.7 + c[6].min(100.0) / 100.0 * 0.27;
        self.reverb_damp = [0.35, 0.25, 0.1][kind];
        self.reverb_mix = c[7].min(100.0) / 100.0;
        self.delay_on = c[8] > 0.0;
        self.delay_fb = c[9].min(100.0) / 100.0 * 0.9;
        let wanted = ((40.0 + c[10].min(100.0) * 9.6) / 1000.0 * sr).round() as usize;
        self.delay_len = wanted.min(self.delay.len() - 2);
        self.delay_mix = c[11].min(100.0) / 100.0;
        self.chorus_on = c[16] > 0.0;
        self.chorus_rate = 0.1 * 50f64.powf(c[17] / 100.0);
        self.chorus_depth = c[18] / 100.0 * 0.008 * sr;
        self.chorus_mix = c[19].min(100.0) / 100.0;
        self.phaser_on = c[20] > 0.0;
        self.phaser_rate = 0.05 * 80f64.powf(c[21] / 100.0);
        self.phaser_depth = c[22].min(100.0) / 100.0;
        self.phaser_mix = c[23].min(100.0) / 100.0;
    }

    /// Forget everything that is still ringing; the settings stay.
    fn clear(&mut self) {
        self.svf = [0.0; 2];
        self.tone_z = 0.0;
        self.delay.fill(0.0);
        self.chorus.fill(0.0);
        self.phaser = [0.0; 4];
        self.phaser_fb = 0.0;
        for comb in &mut self.combs {
            comb.buffer.fill(0.0);
            comb.damped = 0.0;
        }
        for allpass in &mut self.allpasses {
            allpass.buffer.fill(0.0);
        }
    }

    fn process(&mut self, buffer: &mut [f32]) {
        let sr = self.sr;
        for sample in buffer.iter_mut() {
            let mut x = *sample as f64;
            if self.dist_on {
                x = (x * self.drive).tanh();
                self.tone_z += self.tone_a * (x - self.tone_z);
                x = self.tone_z * self.level;
            }
            if self.filter_on {
                // State-variable filter, trapezoidal integration.
                let (g, k) = (self.g, self.k);
                let a1 = 1.0 / (1.0 + g * (g + k));
                let a2 = g * a1;
                let a3 = g * a2;
                let v3 = x - self.svf[1];
                let v1 = a1 * self.svf[0] + a2 * v3;
                let v2 = self.svf[1] + a2 * self.svf[0] + a3 * v3;
                self.svf[0] = 2.0 * v1 - self.svf[0];
                self.svf[1] = 2.0 * v2 - self.svf[1];
                x = match self.filter_type {
                    0 => v2,
                    1 => v1 * k,
                    _ => x - k * v1 - v2,
                };
            }
            if self.chorus_on {
                // A modulated delay: 12 ms plus the depth.
                let len = self.chorus.len();
                self.chorus[self.chorus_w] = x as f32;
                self.chorus_phase = (self.chorus_phase + self.chorus_rate / sr) % 1.0;
                let sweep = 0.5 + 0.5 * (2.0 * PI * self.chorus_phase).sin();
                let mut read = self.chorus_w as f64 - (0.012 * sr + self.chorus_depth * sweep);
                while read < 0.0 {
                    read += len as f64;
                }
                let whole = read as usize;
                let part = read - whole as f64;
                let y = self.chorus[whole % len] as f64 * (1.0 - part)
                    + self.chorus[(whole + 1) % len] as f64 * part;
                self.chorus_w = (self.chorus_w + 1) % len;
                x = x * (1.0 - self.chorus_mix * 0.5) + y * self.chorus_mix * 0.5;
            }
            if self.phaser_on {
                // Four swept all-pass stages with feedback; the sweep moves every 16 samples.
                if self.phaser_n & 15 == 0 {
                    self.phaser_phase = (self.phaser_phase + 16.0 * self.phaser_rate / sr) % 1.0;
                    let sweep = 0.5 + 0.5 * (2.0 * PI * self.phaser_phase).sin();
                    let hz = 300.0 * 2f64.powf(self.phaser_depth * 3.0 * sweep);
                    let t = (PI * hz.min(sr * 0.45) / sr).tan();
                    self.phaser_a = (t - 1.0) / (t + 1.0);
                }
                self.phaser_n = self.phaser_n.wrapping_add(1);
                let mut u = x + self.phaser_fb * 0.5;
                for stage in &mut self.phaser {
                    let y = self.phaser_a * u + *stage as f64;
                    *stage = (u - self.phaser_a * y) as f32;
                    u = y;
                }
                self.phaser_fb = u;
                x = x * (1.0 - self.phaser_mix * 0.5) + u * self.phaser_mix * 0.5;
            }
            if self.delay_on {
                let len = self.delay.len();
                let read = (self.delay_w + len - self.delay_len) % len;
                let y = self.delay[read] as f64;
                self.delay[self.delay_w] = (x + y * self.delay_fb) as f32;
                self.delay_w = (self.delay_w + 1) % len;
                x += y * self.delay_mix;
            }
            if self.reverb_on {
                // Four damped combs and two all-passes (Schroeder, as in Freeverb).
                let mut wet = 0.0;
                let input = x * 0.03;
                for (comb, &len) in self.combs.iter_mut().zip(&self.reverb_len) {
                    let y = comb.buffer[comb.at] as f64;
                    comb.damped = y * (1.0 - self.reverb_damp) + comb.damped * self.reverb_damp;
                    comb.buffer[comb.at] = (input + comb.damped * self.reverb_fb) as f32;
                    comb.at += 1;
                    if comb.at >= len {
                        comb.at = 0;
                    }
                    wet += y;
                }
                for allpass in &mut self.allpasses {
                    let y = allpass.buffer[allpass.at] as f64;
                    allpass.buffer[allpass.at] = (wet + y * 0.5) as f32;
                    wet = y - wet;
                    allpass.at += 1;
                    if allpass.at >= allpass.buffer.len() {
                        allpass.at = 0;
                    }
                }
                x += wet * self.reverb_mix * 3.0;
            }
            *sample = x as f32;
        }
    }
}

#[derive(Clone, Copy, Default)]
struct Op {
    on: bool,
    rates: [u8; 4],
    levels: [u8; 4],
    /// Output level after keyboard scaling and velocity, in envelope units.
    level_units: i32,
    rate_scaling: i32,
    ams: f64,
    /// Frequency in Hz in fixed mode, 0 in ratio mode.
    fixed: f64,
    ratio: f64,
    phase: f64,
    step: f64,
    level: f64,
    target: f64,
    rate: f64,
    rising: bool,
    /// Envelope stage 0..3; 4 when finished.
    stage: u8,
    tremolo: f64,
}

#[derive(Clone, Copy, Default)]
struct PitchEnvelope {
    level: f64,
    target: f64,
    rate: f64,
    rising: bool,
    stage: u8,
    rates: [u8; 4],
    levels: [u8; 4],
}

#[derive(Clone, Copy, Default)]
struct Lfo {
    hz: f64,
    phase: f64,
    wave: u8,
    /// Sample-and-hold value.
    held: f64,
    delay: f64,
    ramp: f64,
    pitch_depth: f64,
    amp_depth: f64,
    age: f64,
}

#[derive(Clone, Copy, Default)]
struct Voice {
    key: u8,
    /// The key is down. `sustained`: it was released while the pedal was down.
    down: bool,
    sustained: bool,
    /// Operators 1..6 at their own index; 0 is unused.
    ops: [Op; 7],
    out: [f64; 7],
    algorithm: Algorithm,
    feedback: f64,
    fb1: f64,
    fb2: f64,
    counter: u32,
    base_hz: f64,
    pitch: PitchEnvelope,
    pitch_active: bool,
    lfo: Lfo,
    lfo_active: bool,
}

/// What the per-voice code needs from the synth.
#[derive(Clone, Copy)]
struct Env {
    sr: f64,
    /// DX7 envelope rates were calibrated at 44.1 kHz.
    rate_k: f64,
    tables: &'static Tables,
}

fn advance(op: &mut Op, stage: u8, env: Env) {
    op.stage = stage;
    if stage > 3 {
        return;
    }
    let s = stage as usize;
    op.target = (((scale_out(op.levels[s]) >> 1) << 6) + op.level_units - 4256).max(16) as f64;
    op.rising = op.target > op.level;
    let q = (((op.rates[s] as i32 * 41) >> 6) + op.rate_scaling).min(63);
    op.rate = (4 + (q & 3)) as f64 * 2f64.powi(2 + (q >> 2)) / 65536.0 * env.rate_k;
}

fn pitch_advance(pitch: &mut PitchEnvelope, stage: u8, env: Env) {
    pitch.stage = stage;
    if stage > 3 {
        return;
    }
    let s = stage as usize;
    pitch.target = PLV[pitch.levels[s].min(99) as usize] as f64;
    pitch.rising = pitch.target > pitch.level;
    // Pitch envelope units (1/32 octave) per sample; 29.2 is fitted to the FM-1, which is 1.37
    // times slower than the DX7's 21.3.
    pitch.rate = PRATE[pitch.rates[s].min(99) as usize] as f64 * 32.0 / (29.2 * env.sr);
}

impl Voice {
    fn release(&mut self, env: Env) {
        self.down = false;
        self.sustained = false;
        for op in &mut self.ops[1..] {
            advance(op, 3, env);
        }
        pitch_advance(&mut self.pitch, 3, env);
    }

    /// Pitch envelope and LFO, every 32 samples (`elapsed` is 0 for the first call of a note).
    fn modulate(&mut self, elapsed: f64, env: Env, random: &mut u64) {
        let gate = self.down || self.sustained;
        let pitch = &mut self.pitch;
        if self.pitch_active && (pitch.stage < 3 || (pitch.stage < 4 && !gate)) {
            let reached = if pitch.rising {
                pitch.level += pitch.rate * elapsed;
                pitch.level >= pitch.target
            } else {
                pitch.level -= pitch.rate * elapsed;
                pitch.level <= pitch.target
            };
            if reached {
                pitch.level = pitch.target;
                pitch_advance(pitch, pitch.stage + 1, env);
            }
        }
        let mut semitones = if self.pitch_active { pitch.level * 12.0 / 32.0 } else { 0.0 };
        let mut tremolo = 0.0;
        if self.lfo_active {
            let lfo = &mut self.lfo;
            lfo.age += elapsed;
            let before = lfo.phase;
            lfo.phase = (lfo.phase + lfo.hz * elapsed / env.sr) % 1.0;
            if lfo.phase < before {
                lfo.held = unit(random) * 2.0 - 1.0;
            }
            let x = lfo.phase;
            let value = match lfo.wave {
                0 if x < 0.5 => 4.0 * x - 1.0,
                0 => 3.0 - 4.0 * x,
                1 => 1.0 - 2.0 * x,
                2 => 2.0 * x - 1.0,
                3 if x < 0.5 => 1.0,
                3 => -1.0,
                4 => (2.0 * PI * x).sin(),
                5 => lfo.held,
                _ => 0.0,
            };
            let gain = ((lfo.age - lfo.delay) / lfo.ramp).clamp(0.0, 1.0);
            semitones += value * gain * lfo.pitch_depth;
            tremolo = (1.0 - value) / 2.0 * gain * lfo.amp_depth;
        }
        let bend = 2f64.powf(semitones / 12.0);
        for op in &mut self.ops[1..] {
            let hz = if op.fixed != 0.0 { op.fixed } else { self.base_hz * op.ratio * bend };
            op.step = hz / env.sr;
            op.tremolo = tremolo * op.ams;
        }
    }

    fn render(&mut self, out: &mut [f32], env: Env, random: &mut u64) {
        let (sin, exp) = (&env.tables.sin, &env.tables.exp);
        let algorithm = self.algorithm;
        let feedback_op = algorithm.feedback as usize;
        // The workbench's version lets a pedal-held voice run into its release; here the pedal
        // holds the envelopes, as a sustain pedal should.
        let gate = self.down || self.sustained;
        for sample in out.iter_mut() {
            if self.counter & 31 == 0 {
                self.modulate(32.0, env, random);
            }
            self.counter = self.counter.wrapping_add(1);
            for k in (1..=6).rev() {
                let op = &mut self.ops[k];
                if op.stage < 3 || (op.stage < 4 && !gate) {
                    let reached = if op.rising {
                        if op.level < 1716.0 {
                            op.level = 1716.0;
                        }
                        op.level += (17.0 - (op.level / 256.0).floor()) * op.rate;
                        op.level >= op.target
                    } else {
                        op.level -= op.rate;
                        op.level <= op.target
                    };
                    if reached {
                        op.level = op.target;
                        advance(op, op.stage + 1, env);
                    }
                }
                if !op.on {
                    self.out[k] = 0.0;
                    continue;
                }
                let mut modulation = 0.0;
                for &from in &algorithm.mods[k][..algorithm.mod_count[k] as usize] {
                    modulation += self.out[from as usize];
                }
                modulation *= 2.0; // a full-level modulator swings the phase two cycles (4π)
                if k == feedback_op && self.feedback != 0.0 {
                    modulation += (self.fb1 + self.fb2) * self.feedback;
                }
                let mut phase = op.phase + modulation;
                phase -= phase.floor();
                let x = phase * SIN_N as f64;
                let xi = (x as usize).min(SIN_N);
                let wave = sin[xi] as f64 + (sin[xi + 1] as f64 - sin[xi] as f64) * (x - xi as f64);
                let level = op.level - op.tremolo;
                let index = if level <= 0.0 {
                    0
                } else if level >= 4096.0 {
                    4096
                } else {
                    level as usize
                };
                let value = wave * exp[index] as f64;
                self.out[k] = value;
                if k == feedback_op {
                    self.fb2 = self.fb1;
                    self.fb1 = value;
                }
                op.phase += op.step;
                if op.phase >= 1.0 {
                    op.phase -= 1.0;
                }
            }
            let mut sum = 0.0;
            for &carrier in &algorithm.carriers[..algorithm.carrier_count as usize] {
                sum += self.out[carrier as usize];
            }
            *sample += sum as f32;
        }
    }

    fn finished(&self) -> bool {
        if self.down || self.sustained {
            return false;
        }
        let carriers = &self.algorithm.carriers[..self.algorithm.carrier_count as usize];
        carriers.iter().all(|&c| {
            let op = &self.ops[c as usize];
            !(op.on && op.stage < 4 && op.level > 900.0)
        })
    }
}

/// Uniform in 0.0..1.0 (xorshift64*), for the sample-and-hold LFO.
fn unit(state: &mut u64) -> f64 {
    *state ^= *state >> 12;
    *state ^= *state << 25;
    *state ^= *state >> 27;
    (state.wrapping_mul(0x2545_F491_4F6C_DD1D) >> 11) as f64 / (1u64 << 53) as f64
}

#[derive(Clone, Copy)]
struct Queued {
    frame: u64,
    msg: Msg,
}

pub struct Synth {
    env: Env,
    /// The edit buffer: what the next note will sound like.
    edit: [u8; EDIT_SIZE],
    voices: Vec<Voice>,
    queue: VecDeque<Queued>,
    /// Messages at the head of the queue that were asked for "now" during the current block.
    urgent: usize,
    sustain: bool,
    fx: Fx,
    fx_channel: u8,
    /// The firmware emulated: Baud Girl's FM-1+VA rather than stock M-VAVE.
    va: bool,
    /// Master volume, FM-1+VA only (CC 7).
    master: f64,
    /// Frame of the next sample to render.
    now: u64,
    lowpass: f64,
    lowpass_a: f64,
    /// The output high-pass: its coefficient, last input and last output.
    dc_r: f64,
    dc_x: f64,
    dc_y: f64,
    quiet_frames: u64,
    random: u64,
}

impl Synth {
    pub fn new(sample_rate: f32) -> Self {
        let sr = sample_rate.max(1.0) as f64;
        Synth {
            env: Env { sr, rate_k: 44100.0 / sr, tables: tables() },
            edit: dx7::init_voice(),
            voices: Vec::with_capacity(MAX_VOICES),
            queue: VecDeque::with_capacity(QUEUE),
            urgent: 0,
            sustain: false,
            fx: Fx::new(sr),
            fx_channel: 1,
            va: false,
            master: 1.0,
            now: 0,
            lowpass: 0.0,
            lowpass_a: 1.0 - (-2.0 * PI * OUT_LP_HZ / sr).exp(),
            dc_r: (-2.0 * PI * OUT_HP_HZ / sr).exp(),
            dc_x: 0.0,
            dc_y: 0.0,
            quiet_frames: u64::MAX / 2,
            random: 0x9E37_79B9_7F4A_7C15,
        }
    }

    pub fn sample_rate(&self) -> f32 {
        self.env.sr as f32
    }

    /// Frame of the next sample `render` will produce.
    pub fn now(&self) -> u64 {
        self.now
    }

    /// Which firmware to behave as. Differences: detune 0.9 instead of 2.8 cents per step, the
    /// LFO follows the voice instead of running at a fixed 5.8 Hz, and CC 7/76/77/78 work.
    pub fn set_firmware_va(&mut self, va: bool) {
        if self.va != va {
            self.va = va;
            if !va {
                self.master = 1.0;
            }
        }
    }

    /// Effect channel, 0-based: CC 0..23 on it set the effects.
    pub fn set_fx_channel(&mut self, channel: u8) {
        self.fx_channel = channel & 0x0F;
    }

    pub fn full(&self) -> bool {
        self.queue.len() >= QUEUE
    }

    /// Schedule a message for `frame`; one that is already late plays at once. Returns false,
    /// taking nothing, when the queue is full.
    pub fn queue(&mut self, frame: u64, msg: &Msg) -> bool {
        if self.full() {
            return false;
        }
        let frame = frame.max(self.now);
        let at = self.queue.partition_point(|queued| queued.frame <= frame);
        self.queue.insert(at, Queued { frame, msg: *msg });
        true
    }

    /// Apply a message at the very next sample, ahead of everything queued for the same moment
    /// but after earlier `queue_now` messages. This is the hardware's "send now".
    pub fn queue_now(&mut self, msg: &Msg) -> bool {
        if self.full() {
            return false;
        }
        self.queue.insert(self.urgent, Queued { frame: self.now, msg: *msg });
        self.urgent += 1;
        true
    }

    /// True while there is something to hear or to do; `render` is cheap when there is not.
    pub fn active(&self) -> bool {
        !self.voices.is_empty() || !self.queue.is_empty() || !self.idle()
    }

    fn idle(&self) -> bool {
        self.quiet_frames as f64 > IDLE_SECONDS * self.env.sr
    }

    pub fn voice_count(&self) -> usize {
        self.voices.len()
    }

    /// Apply everything queued, whenever it was due, then cut all sound. The edit buffer, the
    /// effect settings and the volume stay: they are what the sender believes the synth holds.
    pub fn silence(&mut self) {
        while let Some(queued) = self.queue.pop_front() {
            self.midi(queued.msg.data());
        }
        self.urgent = 0;
        self.voices.clear();
        self.sustain = false;
        self.fx.clear();
        self.lowpass = 0.0;
        (self.dc_x, self.dc_y) = (0.0, 0.0);
        self.quiet_frames = u64::MAX / 2;
    }

    fn midi(&mut self, data: &[u8]) {
        let Some(&status) = data.first() else { return };
        let kind = status & 0xF0;
        let byte = |index: usize| data.get(index).copied().unwrap_or(0);
        let (a, b) = (byte(1), byte(2));
        if status == 0xF0 {
            return self.sysex(data);
        }
        if kind == 0x90 && b > 0 {
            return self.note_on(a, b);
        }
        if kind == 0x80 || kind == 0x90 {
            return self.note_off(a);
        }
        if kind != 0xB0 {
            return; // program change needs a stored bank; pitch bend is not emulated
        }
        if status & 0x0F == self.fx_channel && a < 24 {
            return self.fx.set(a, b);
        }
        if self.va {
            // FM-1+VA controllers on the note channel.
            let scaled = (b as f64 * 99.0 / 127.0).round() as u8;
            match a {
                // Measured: 64 = -6.5 dB, 32 = -12 dB.
                7 => return self.master = (b as f64 / 127.0).powf(1.09),
                76 => {
                    self.edit[dx7::global::LFS] = scaled;
                    let hz = lfo_hz(scaled);
                    for voice in &mut self.voices {
                        voice.lfo.hz = hz;
                    }
                    return;
                }
                77 => {
                    self.edit[dx7::global::LPMD] = scaled;
                    let depth = scaled as f64 / 99.0 * pms(self.edit[dx7::global::LPMS]);
                    for voice in &mut self.voices {
                        voice.lfo.pitch_depth = depth;
                        voice.lfo_active = true;
                    }
                    return;
                }
                78 => return self.edit[138] = scaled,
                _ => {}
            }
        }
        if a == 64 {
            self.sustain = b >= 64;
            if !self.sustain {
                let env = self.env;
                for voice in self.voices.iter_mut().filter(|v| v.sustained && !v.down) {
                    voice.release(env);
                }
            }
        }
    }

    fn sysex(&mut self, data: &[u8]) {
        // Parameter change: F0 43 1n gg pp dd F7.
        if data.len() >= 7 && data[1] == 0x43 && data[2] & 0xF0 == 0x10 {
            let index = ((data[3] as usize & 3) << 7) | data[4] as usize;
            if index <= OP_MASK {
                self.edit[index] = data[5] & 0x7F;
            }
        }
    }

    fn note_on(&mut self, key: u8, velocity: u8) {
        let (env, p) = (self.env, &self.edit);
        if self.voices.len() >= MAX_VOICES {
            // Steal a released voice first, else the oldest.
            let released = self.voices.iter().position(|v| !v.down && !v.sustained);
            self.voices.remove(released.unwrap_or(0));
        }
        let note = (key as i32 + p[dx7::TRANSPOSE] as i32 - 24).clamp(0, 127);
        let feedback = p[dx7::global::FB];
        let mut voice = Voice {
            key,
            down: true,
            algorithm: env.tables.algorithms[(p[dx7::global::ALG] & 31) as usize],
            feedback: if feedback != 0 { FB_SCALE * 2f64.powi(feedback as i32 - 8) } else { 0.0 },
            base_hz: 440.0 * 2f64.powf((note - 69) as f64 / 12.0),
            ..Voice::default()
        };
        let rate_scaling = |sensitivity: u8| ((note / 3 - 7).clamp(0, 31) * sensitivity as i32) >> 3;
        // Keyboard level scaling: curve 0 -lin, 1 -exp, 2 +exp, 3 +lin.
        let curve = |group: i32, depth: u8, curve: u8| {
            let depth = depth as i32;
            let amount = if curve == 0 || curve == 3 {
                (group * depth * 329) >> 12
            } else {
                (EXPSCALE[group.min(32) as usize] * depth * 329) >> 15
            };
            if curve < 2 {
                -amount
            } else {
                amount
            }
        };
        for n in 1..=6 {
            let o = (6 - n) * 21;
            let offset = note - p[o + 8] as i32 - 17;
            let scaling = if offset >= 0 {
                curve((offset + 1) / 3, p[o + 10], p[o + 12])
            } else {
                curve(-(offset - 1) / 3, p[o + 9], p[o + 11])
            };
            let mut level_units = (scale_out(p[o + 16]) + scaling).clamp(0, 127) << 5;
            // Velocity: the DX7's table, but the FM-1 never boosts above the stored level
            // (measured about 0.87 dB lower per sensitivity step), hence 257 rather than 239.
            level_units += ((p[o + 15] as i32 * (VEL[(velocity.min(127) >> 1) as usize] - 257) + 7) >> 3) << 4;
            let (mode, coarse, fine, detune) = (p[o + 17], p[o + 18], p[o + 19] as f64, p[o + 20] as f64);
            let cents = if self.va { 0.9 } else { 2.8 };
            let op = &mut voice.ops[n];
            *op = Op {
                on: (p[OP_MASK] >> (6 - n)) & 1 == 1,
                rates: [p[o], p[o + 1], p[o + 2], p[o + 3]],
                levels: [p[o + 4], p[o + 5], p[o + 6], p[o + 7]],
                level_units,
                rate_scaling: rate_scaling(p[o + 13]),
                ams: ams(p[o + 14]),
                fixed: if mode != 0 { 10f64.powi((coarse & 3) as i32) * 10f64.powf(fine / 100.0) } else { 0.0 },
                ratio: (if coarse == 0 { 0.5 } else { coarse as f64 })
                    * (1.0 + fine / 100.0)
                    * 2f64.powf((detune - 7.0) * cents / 1200.0),
                ..Op::default()
            };
            advance(op, 0, env);
        }
        voice.pitch = PitchEnvelope {
            level: PLV[p[133].min(99) as usize] as f64,
            rates: [p[126], p[127], p[128], p[129]],
            levels: [p[130], p[131], p[132], p[133]],
            ..PitchEnvelope::default()
        };
        voice.pitch_active = voice.pitch.levels.iter().any(|&level| level != 50);
        pitch_advance(&mut voice.pitch, 0, env);
        // The LFO runs free: its phase at note-on depends on the time.
        let hz = if self.va { lfo_hz(p[dx7::global::LFS]) } else { LFO_HZ };
        let delay = (p[138] as f64 / 99.0).powi(2);
        voice.lfo = Lfo {
            hz,
            phase: (self.now as f64 * hz / env.sr) % 1.0,
            wave: if self.va { p[142] } else { 4 },
            held: 0.0,
            delay: if self.va { delay * 4.0 * env.sr } else { 0.0 },
            ramp: if self.va { (delay * 2.0 * env.sr).max(1.0) } else { 1.0 },
            pitch_depth: p[dx7::global::LPMD] as f64 / 99.0 * pms(p[dx7::global::LPMS]),
            amp_depth: p[140] as f64 / 99.0,
            age: 0.0,
        };
        voice.lfo_active = voice.lfo.pitch_depth > 0.0 || voice.lfo.amp_depth > 0.0;
        voice.modulate(0.0, env, &mut self.random);
        self.voices.push(voice);
    }

    fn note_off(&mut self, key: u8) {
        let (env, sustain) = (self.env, self.sustain);
        for voice in self.voices.iter_mut().filter(|v| v.key == key && v.down) {
            if sustain {
                voice.sustained = true;
                voice.down = false;
            } else {
                voice.release(env);
            }
        }
    }

    /// Render the next `out.len()` samples (mono). Queued messages take effect at their sample.
    pub fn render(&mut self, out: &mut [f32]) {
        self.render_raw(out);
        if self.idle() && self.dc_y.abs() < 1e-12 {
            (self.dc_x, self.dc_y) = (0.0, 0.0);
            return;
        }
        for sample in out.iter_mut() {
            let x = *sample as f64;
            self.dc_y = x - self.dc_x + self.dc_r * self.dc_y;
            self.dc_x = x;
            // Below -200 dB the filter's own decay is cut off, so that silence is exact zeros.
            if self.dc_y.abs() < 1e-10 {
                self.dc_y = 0.0;
            }
            *sample = self.dc_y as f32;
        }
    }

    /// The same without the output high-pass: exactly what the workbench's synth renders.
    pub fn render_raw(&mut self, out: &mut [f32]) {
        out.fill(0.0);
        self.urgent = 0;
        let (start, n) = (self.now, out.len());
        if self.queue.is_empty() && self.voices.is_empty() && self.idle() {
            self.now += n as u64;
            return;
        }
        let env = self.env;
        let mut i = 0;
        while i < n {
            self.now = start + i as u64;
            while self.queue.front().is_some_and(|queued| queued.frame <= self.now) {
                if let Some(queued) = self.queue.pop_front() {
                    self.midi(queued.msg.data());
                }
            }
            let j = match self.queue.front() {
                Some(next) => ((next.frame - start) as usize).clamp(i + 1, n),
                None => n,
            };
            for voice in &mut self.voices {
                voice.render(&mut out[i..j], env, &mut self.random);
            }
            i = j;
        }
        self.now = start + n as u64;
        self.voices.retain(|voice| !voice.finished());

        // The unit's output rolls off the treble: a one-pole low-pass.
        let gain = 0.2 * VOLUME * self.master;
        for sample in out.iter_mut() {
            self.lowpass += self.lowpass_a * (*sample as f64 - self.lowpass);
            *sample = (self.lowpass * gain) as f32;
        }
        self.fx.process(out);
        let mut peak = 0f32;
        for sample in out.iter_mut() {
            *sample = (*sample as f64).tanh() as f32;
            peak = peak.max(sample.abs());
        }
        if peak > QUIET || !self.voices.is_empty() {
            self.quiet_frames = 0;
        } else {
            self.quiet_frames = self.quiet_frames.saturating_add(n as u64);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::voicegen::{random_voice, Rng, Style};

    const SR: f32 = 44100.0;

    fn short(status: u8, a: u8, b: u8) -> Msg {
        Msg { offset: 0, len: 3, bytes: [status, a, b, 0, 0, 0, 0] }
    }

    fn param(index: usize, value: u8) -> Msg {
        Msg { offset: 0, len: 7, bytes: dx7::param_change(0, index, value) }
    }

    fn load(synth: &mut Synth, voice: &dx7::Voice, frame: u64) {
        for (index, &value) in voice.iter().enumerate() {
            assert!(synth.queue(frame, &param(index, value)));
        }
    }

    fn render(synth: &mut Synth, seconds: f32) -> Vec<f32> {
        let mut out = vec![0f32; (seconds * synth.sample_rate()) as usize];
        for block in out.chunks_mut(128) {
            synth.render(block);
        }
        out
    }

    fn rms(samples: &[f32]) -> f32 {
        (samples.iter().map(|s| s * s).sum::<f32>() / samples.len().max(1) as f32).sqrt()
    }

    /// Strength of `hz` in `samples`, as the amplitude of a sine of that frequency (Hann window,
    /// so that a strong neighbouring tone does not leak into the answer).
    fn tone(samples: &[f32], hz: f32, sr: f32) -> f32 {
        let (mut re, mut im, mut weight) = (0f64, 0f64, 0f64);
        for (i, &s) in samples.iter().enumerate() {
            let window = 0.5 - 0.5 * (2.0 * PI * i as f64 / samples.len() as f64).cos();
            let angle = 2.0 * PI * hz as f64 * i as f64 / sr as f64;
            re += window * s as f64 * angle.cos();
            im += window * s as f64 * angle.sin();
            weight += window;
        }
        (2.0 * (re * re + im * im).sqrt() / weight) as f32
    }

    #[test]
    fn init_voice_plays_a_sine_at_the_notes_pitch_and_stops_when_released() {
        let mut synth = Synth::new(SR);
        assert!(!synth.active());
        synth.queue(0, &short(0x90, 69, 100));
        synth.queue(22050, &short(0x80, 69, 0));
        let out = render(&mut synth, 1.0);
        let held = &out[4410..22050];
        // One carrier at full level: 0.2 x the volume before the treble roll-off, and nothing else.
        assert!((0.085..0.092).contains(&tone(held, 440.0, SR)), "{}", tone(held, 440.0, SR));
        assert!(tone(held, 880.0, SR) < 0.002 && tone(held, 466.16, SR) < 0.002);
        assert!((rms(held) - tone(held, 440.0, SR) / 2f32.sqrt()).abs() < 0.002);
        // Before the note there is silence; after the release the voice is freed and silent.
        assert_eq!(synth.voice_count(), 0);
        assert!(rms(&out[39690..]) < 1e-4, "{}", rms(&out[39690..]));
        let tail = render(&mut synth, 3.0);
        assert!(tail[44100..].iter().all(|&s| s == 0.0));
        assert!(!synth.active());
    }

    #[test]
    fn messages_take_effect_at_their_sample() {
        let mut synth = Synth::new(SR);
        synth.queue(1000, &short(0x90, 60, 127));
        let out = render(&mut synth, 0.1);
        assert!(out[..1000].iter().all(|&s| s == 0.0));
        assert!(out[1001..1100].iter().any(|&s| s.abs() > 1e-4));
        // A message for the past plays at once rather than never.
        let mut late = Synth::new(SR);
        render(&mut late, 0.05);
        late.queue(3, &short(0x90, 60, 127));
        assert!(rms(&render(&mut late, 0.05)) > 0.01);
    }

    #[test]
    fn a_parameter_change_only_affects_the_next_note() {
        let mut synth = Synth::new(SR);
        let level = dx7::op_index(1, dx7::op::OL);
        synth.queue(0, &short(0x90, 60, 100));
        synth.queue(4410, &param(level, 70)); // 29 steps down: about -22 dB
        synth.queue(8820, &short(0x90, 72, 100));
        let out = render(&mut synth, 0.4);
        let first = tone(&out[9000..17000], 261.63, SR);
        let second = tone(&out[9000..17000], 523.25, SR);
        assert!(first > 0.07, "{first}");
        let db = 20.0 * (second / first).log10();
        // 0.75 dB per level step, plus about 0.03 dB of treble roll-off an octave up.
        assert!((-22.3..-21.3).contains(&db), "{db}");
    }

    #[test]
    fn transpose_velocity_and_the_operator_switches_follow_the_edit_buffer() {
        let mut voice = dx7::init_voice();
        voice[dx7::TRANSPOSE] = 36; // an octave up
        voice[dx7::op_index(1, dx7::op::KVS)] = 7;
        let mut synth = Synth::new(SR);
        load(&mut synth, &voice, 0);
        synth.queue(10, &short(0x90, 57, 127));
        let loud = render(&mut synth, 0.3);
        assert!(tone(&loud[4410..], 440.0, SR) > 0.06);

        let mut soft = Synth::new(SR);
        load(&mut soft, &voice, 0);
        soft.queue(10, &short(0x90, 57, 30));
        let quiet = render(&mut soft, 0.3);
        assert!(tone(&quiet[4410..], 440.0, SR) < 0.25 * tone(&loud[4410..], 440.0, SR));

        // Operator mask 0: every operator off, nothing sounds.
        voice[OP_MASK] = 0;
        let mut off = Synth::new(SR);
        load(&mut off, &voice, 0);
        off.queue(10, &short(0x90, 57, 127));
        assert_eq!(rms(&render(&mut off, 0.1)), 0.0);
    }

    #[test]
    fn sustain_holds_released_notes_and_seventeenth_note_steals_a_voice() {
        let mut synth = Synth::new(SR);
        synth.queue(0, &short(0xB0, 64, 127));
        synth.queue(10, &short(0x90, 60, 100));
        synth.queue(2000, &short(0x80, 60, 0));
        let out = render(&mut synth, 0.5);
        assert!(rms(&out[17640..]) > 0.03); // still sounding: the pedal holds it
        synth.queue(0, &short(0xB0, 64, 0));
        let out = render(&mut synth, 0.5);
        assert!(rms(&out[17640..]) < 1e-4 && synth.voice_count() == 0);

        for key in 0..20 {
            synth.queue(0, &short(0x90, 40 + key, 100));
        }
        render(&mut synth, 0.01);
        assert_eq!(synth.voice_count(), MAX_VOICES);
        synth.silence();
        assert_eq!(synth.voice_count(), 0);
    }

    #[test]
    fn effects_listen_on_their_channel_only_and_every_one_changes_the_sound() {
        let dry = {
            let mut synth = Synth::new(SR);
            synth.queue(0, &short(0xB0, 4, 127)); // reverb on, but on the note channel: ignored
            synth.queue(0, &short(0x90, 60, 100));
            synth.queue(4410, &short(0x80, 60, 0));
            render(&mut synth, 1.0)
        };
        assert!(rms(&dry[22050..]) < 1e-4);
        // Switch, first value controller, value: reverb, delay, distortion, chorus, phaser, filter.
        for (switch, setting, value) in [(4, 7, 100), (8, 11, 100), (12, 13, 100), (16, 19, 100), (20, 23, 100), (0, 2, 30)] {
            let mut synth = Synth::new(SR);
            synth.set_fx_channel(3);
            synth.queue(0, &short(0xB3, switch, 127));
            synth.queue(0, &short(0xB3, setting, value));
            synth.queue(0, &short(0x90, 60, 100));
            synth.queue(4410, &short(0x80, 60, 0));
            let wet = render(&mut synth, 1.0);
            let difference: Vec<f32> = wet.iter().zip(&dry).map(|(a, b)| a - b).collect();
            assert!(rms(&difference) > 1e-3, "effect {switch}: {}", rms(&difference));
            assert!(wet.iter().all(|s| s.is_finite() && s.abs() <= 1.0));
            if switch == 4 || switch == 8 {
                assert!(rms(&wet[22050..]) > 1e-4, "effect {switch} leaves a tail");
            }
        }
    }

    #[test]
    fn va_firmware_follows_the_lfo_speed_and_cc_7_while_stock_ignores_both() {
        let mut voice = dx7::init_voice();
        voice[dx7::global::LFS] = 62; // 10.4 Hz on FM-1+VA
        voice[140] = 99; // amplitude modulation depth
        voice[dx7::op_index(1, 14)] = 3; // operator 1 fully sensitive: tremolo to silence
        let play = |va: bool, volume: Option<u8>| {
            let mut synth = Synth::new(SR);
            synth.set_firmware_va(va);
            load(&mut synth, &voice, 0);
            if let Some(volume) = volume {
                synth.queue(0, &short(0xB0, 7, volume));
            }
            synth.queue(10, &short(0x90, 69, 100));
            render(&mut synth, 2.0)
        };
        // Tremolo rate: strength of the loudness envelope at each candidate frequency.
        let rate = |out: &[f32], hz: f32| {
            let envelope: Vec<f32> = out[4410..].chunks(64).map(rms).collect();
            let mean = envelope.iter().sum::<f32>() / envelope.len() as f32;
            let centred: Vec<f32> = envelope.iter().map(|e| e - mean).collect();
            tone(&centred, hz, SR / 64.0)
        };
        let (stock, va) = (play(false, None), play(true, None));
        assert!(rate(&stock, 5.8) > 4.0 * rate(&stock, 10.4));
        assert!(rate(&va, 10.4) > 4.0 * rate(&va, 5.8));
        // CC 7 at 64 is about -6.5 dB on FM-1+VA and does nothing on stock.
        let ratio = rms(&play(true, Some(64))) / rms(&va);
        assert!((0.44..0.50).contains(&ratio), "{ratio}");
        assert_eq!(play(false, Some(64)), stock);
    }

    #[test]
    fn the_output_carries_no_dc_offset() {
        // Algorithm 31: operator 6, bent by its own feedback, modulates operator 5 at the same
        // pitch but one detune step (2.8 cents, 0.4 Hz at this note) away. As the two drift apart
        // in phase the wave becomes lopsided: an offset that swells and fades at 0.4 Hz.
        let mut voice = dx7::init_voice();
        voice[dx7::global::ALG] = 30;
        voice[dx7::global::FB] = 7;
        voice[dx7::op_index(6, dx7::op::OL)] = 85;
        voice[dx7::op_index(6, dx7::op::DT)] = 8;
        voice[dx7::op_index(5, dx7::op::OL)] = 99;
        voice[dx7::op_index(1, dx7::op::OL)] = 0;
        let play = |raw: bool| {
            let mut synth = Synth::new(SR);
            load(&mut synth, &voice, 0);
            synth.queue(10, &short(0x90, 60, 100));
            let mut out = vec![0f32; 44100];
            for block in out.chunks_mut(128) {
                if raw {
                    synth.render_raw(block);
                } else {
                    synth.render(block);
                }
            }
            out
        };
        let mean = |samples: &[f32]| samples.iter().sum::<f32>() / samples.len() as f32;
        let (raw, blocked) = (play(true), play(false));
        let (raw, blocked) = (&raw[22050..], &blocked[22050..]);
        assert!(mean(raw).abs() > 0.1 * rms(raw), "the workbench's render has the offset: {} of {}", mean(raw), rms(raw));
        // A wander this slow is cut by more than 30 dB.
        assert!(mean(blocked).abs() < 0.03 * mean(raw).abs(), "{} of {}", mean(blocked), mean(raw));
        // What is left is the same sound: the same strength at the note's pitch.
        let ratio = tone(blocked, 261.63, SR) / tone(raw, 261.63, SR);
        assert!((0.99..1.0).contains(&ratio), "{ratio}");
    }

    #[test]
    fn the_bass_rolls_off_as_the_units_output_does() {
        // The init voice on low notes, against the measured unit: first order, 20 Hz.
        let level = |note: u8, hz: f32| {
            let mut synth = Synth::new(SR);
            synth.queue(0, &short(0x90, note, 100));
            let out = render(&mut synth, 2.0);
            20.0 * tone(&out[22050..], hz, SR).log10()
        };
        let reference = level(60, 261.63);
        for (note, hz, measured) in [(12u8, 16.35f32, -3.9f32), (24, 32.70, -1.4), (36, 65.41, -0.4)] {
            let ours = level(note, hz) - reference;
            assert!((ours - measured).abs() < 0.25, "note {note}: {ours} dB, the unit {measured} dB");
        }
    }

    #[test]
    fn urgent_messages_go_first_and_silence_keeps_what_was_queued() {
        let mut synth = Synth::new(SR);
        synth.queue(0, &short(0x90, 60, 100));
        // A release asked for "now" lands before the note queued for the same sample.
        synth.queue_now(&short(0x80, 60, 0));
        render(&mut synth, 0.01);
        assert_eq!(synth.voice_count(), 1);

        // A parameter still waiting when the synth is silenced is applied, not lost.
        synth.queue(1_000_000, &param(dx7::global::ALG, 31));
        synth.silence();
        assert_eq!((synth.voice_count(), synth.edit[dx7::global::ALG]), (0, 31));
        assert!(render(&mut synth, 0.01).iter().all(|&s| s == 0.0));

        let mut full = Synth::new(SR);
        for _ in 0..QUEUE {
            assert!(full.queue(5, &short(0xB0, 1, 1)));
        }
        assert!(full.full() && !full.queue(5, &short(0xB0, 1, 1)) && !full.queue_now(&short(0xB0, 1, 1)));
    }

    /// One render for both synths: sample rate, length, firmware, effect channel, timed messages.
    struct Scenario {
        name: &'static str,
        sr: f32,
        len: usize,
        va: bool,
        fx_channel: u8,
        events: Vec<(u64, Msg)>,
    }

    fn scenarios() -> Vec<Scenario> {
        let mut rng = Rng::new(20261001);
        let mut list = Vec::new();
        let whole = |voice: &dx7::Voice, at: u64| -> Vec<(u64, Msg)> {
            voice.iter().enumerate().map(|(index, &value)| (at, param(index, value))).collect()
        };
        let ms = |sr: f32, ms: f32| (ms / 1000.0 * sr) as u64;

        list.push(Scenario {
            name: "init voice",
            sr: 22050.0,
            len: 22050,
            va: false,
            fx_channel: 1,
            events: vec![(0, short(0x90, 69, 100)), (11025, short(0x80, 69, 0))],
        });
        // Chords of generated voices: every style, a spread of algorithms, three sample rates.
        for (index, style) in Style::ALL.into_iter().enumerate() {
            let sr = [22050.0, 44100.0, 48000.0][index % 3];
            let mut voice = random_voice(style, &mut rng);
            voice[dx7::global::ALG] = [0, 4, 15, 17, 21, 31][index];
            let mut events = whole(&voice, 0);
            for (n, (key, velocity)) in [(48u8, 100u8), (60, 64), (67, 127), (88, 30)].into_iter().enumerate() {
                events.push((ms(sr, 10.0 + 40.0 * n as f32), short(0x90, key, velocity)));
                events.push((ms(sr, 400.0 + 20.0 * n as f32), short(0x80, key, 0)));
            }
            list.push(Scenario { name: "chord", sr, len: (sr * 0.7) as usize, va: index % 2 == 1, fx_channel: 1, events });
        }
        // Voice switching by parameter differences between notes, as kits and speech do.
        let (a, b) = (random_voice(Style::Pluck, &mut rng), random_voice(Style::Bell, &mut rng));
        let mut events = whole(&a, 0);
        for k in 0..8u64 {
            let (voice, previous) = if k % 2 == 1 { (&b, &a) } else { (&a, &b) };
            let at = k * 110;
            if k > 0 {
                for (index, (&value, &old)) in voice.iter().zip(previous).enumerate() {
                    if value != old {
                        events.push((ms(44100.0, at as f32 + 1.0), param(index, value)));
                    }
                }
            }
            events.push((ms(44100.0, at as f32 + 2.0), short(0x90, 60 + k as u8, 100)));
            events.push((ms(44100.0, at as f32 + 90.0), short(0x80, 60 + k as u8, 0)));
        }
        list.push(Scenario { name: "switching", sr: 44100.0, len: 44100, va: false, fx_channel: 1, events });
        // Every effect at once, on effect channel 3, changed while a note sounds.
        let mut events = whole(&random_voice(Style::Keys, &mut rng), 0);
        for (controller, value) in [
            (0, 1), (1, 0), (2, 70), (3, 4), (4, 1), (6, 70), (7, 40), (8, 1), (10, 20), (11, 40), (12, 1),
            (13, 40), (16, 1), (18, 60), (19, 50), (20, 1), (22, 60), (23, 50),
        ] {
            events.push((0, short(0xB2, controller, value)));
        }
        events.extend([
            (441, short(0x90, 60, 100)),
            (9000, short(0xB2, 1, 2)),
            (9000, short(0xB2, 5, 2)),
            (9000, short(0xB2, 2, 100)),
            (13230, short(0x80, 60, 0)),
        ]);
        list.push(Scenario { name: "effects", sr: 44100.0, len: 44100, va: false, fx_channel: 2, events });
        // FM-1+VA: the LFO follows the voice and CC 76/77/78, CC 7 is the volume; pitch envelope.
        for wave in 0..5u8 {
            let mut voice = random_voice(Style::Pad, &mut rng);
            voice[142] = wave;
            voice[dx7::global::LFS] = 70;
            voice[138] = 20;
            voice[dx7::global::LPMD] = 60;
            voice[140] = 40;
            voice[dx7::global::LPMS] = 5;
            voice[dx7::op_index(1, 14)] = 2;
            voice[130..134].copy_from_slice(&[60, 40, 50, 55]);
            voice[126..130].copy_from_slice(&[90, 70, 80, 85]);
            let mut events = whole(&voice, 0);
            events.extend([
                (1, short(0xB0, 7, 64)),
                (220, short(0x90, 69, 100)),
                (4000, short(0xB0, 76, 100)),
                (6000, short(0xB0, 77, 30)),
                (6000, short(0xB0, 78, 90)),
                (8000, short(0x90, 57, 80)),
                (15000, short(0x80, 69, 0)),
                (16000, short(0x80, 57, 0)),
            ]);
            list.push(Scenario { name: "va lfo", sr: 22050.0, len: 22050, va: true, fx_channel: 1, events });
        }
        list
    }

    /// The workbench's Software FM-1 renders the same messages in Node: this port must produce
    /// the same samples. The two differ only in the last bits of `sin`, `pow` and `tanh`.
    #[test]
    fn renders_match_the_workbench_synth_in_node() {
        use std::io::Write;
        use std::process::{Command, Stdio};

        // FM1_REQUIRE_NODE (set by the build workflow) turns a skip into a failure, so that a
        // green build means the comparison ran.
        let required = std::env::var_os("FM1_REQUIRE_NODE").is_some();
        let Some(app) = crate::speech::app_dir() else {
            assert!(!required, "the workbench's app folder was not found");
            eprintln!("workbench not found; skipped");
            return;
        };
        if !Command::new("node").arg("--version").output().is_ok_and(|o| o.status.success()) {
            assert!(!required, "node is not installed");
            eprintln!("node not installed; skipped");
            return;
        }
        let script = r#"
            const fs = require('fs'), path = require('path');
            const { FM1Core, FM1T } = require(path.join(process.argv[1], 'fm1-synth.js'));
            const out = { tables: { VEL: FM1T.VEL, EXPSCALE: FM1T.EXPSCALE, PLV: FM1T.PLV, PRATE: FM1T.PRATE }, renders: [] };
            for (const s of JSON.parse(fs.readFileSync(0, 'utf8'))) {
              const core = new FM1Core(s.sr); core.profile = s.va ? 'va' : 'stock'; core.fxCh = s.fxCh; core.volume = s.volume;
              for (const [f, ...b] of s.events) core.queue(b, f);
              const buf = new Float32Array(s.len), blk = new Float32Array(128);
              for (let f = 0; f < s.len; f += 128) { core.render(blk, f); buf.set(blk.subarray(0, Math.min(128, s.len - f)), f); }
              out.renders.push(Array.from(buf));
            }
            process.stdout.write(JSON.stringify(out));"#;
        let scenarios = scenarios();
        let job: Vec<serde_json::Value> = scenarios
            .iter()
            .map(|s| {
                let events: Vec<Vec<u64>> = s
                    .events
                    .iter()
                    .map(|(frame, msg)| std::iter::once(*frame).chain(msg.data().iter().map(|&b| b as u64)).collect())
                    .collect();
                serde_json::json!({ "sr": s.sr, "len": s.len, "va": s.va, "fxCh": s.fx_channel, "volume": VOLUME, "events": events })
            })
            .collect();
        let mut node = Command::new("node")
            .args(["-e", script])
            .arg(&app)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .unwrap();
        let mut stdin = node.stdin.take().unwrap();
        let text = serde_json::to_string(&job).unwrap();
        let writer = std::thread::spawn(move || stdin.write_all(text.as_bytes()));
        let output = node.wait_with_output().unwrap();
        writer.join().unwrap().unwrap();
        assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
        let reference: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();

        let table = |name: &str| -> Vec<i64> {
            reference["tables"][name].as_array().unwrap().iter().map(|v| v.as_i64().unwrap()).collect()
        };
        let ours = |values: &[i32]| values.iter().map(|&v| v as i64).collect::<Vec<_>>();
        assert_eq!(table("VEL"), ours(&VEL));
        assert_eq!(table("EXPSCALE"), ours(&EXPSCALE));
        assert_eq!(table("PLV"), ours(&PLV));
        assert_eq!(table("PRATE"), ours(&PRATE));

        for (scenario, theirs) in scenarios.iter().zip(reference["renders"].as_array().unwrap()) {
            let theirs: Vec<f32> = theirs.as_array().unwrap().iter().map(|v| v.as_f64().unwrap() as f32).collect();
            let mut synth = Synth::new(scenario.sr);
            synth.set_firmware_va(scenario.va);
            synth.set_fx_channel(scenario.fx_channel);
            for (frame, msg) in &scenario.events {
                assert!(synth.queue(*frame, msg));
            }
            let mut mine = vec![0f32; scenario.len];
            for block in mine.chunks_mut(128) {
                synth.render_raw(block);
            }
            assert_eq!(mine.len(), theirs.len());
            let worst = mine.iter().zip(&theirs).map(|(a, b)| (a - b).abs()).fold(0f32, f32::max);
            let level = rms(&theirs);
            eprintln!("{:<10} {:>6} Hz  rms {level:.4}  worst difference {worst:.2e}", scenario.name, scenario.sr);
            assert!(level > 0.002, "{}: the reference render is silent", scenario.name);
            assert!(worst < 1e-4, "{}: differs from the workbench by {worst}", scenario.name);
        }
    }

    #[test]
    fn every_algorithm_and_random_voices_render_bounded_at_any_sample_rate() {
        let mut rng = Rng::new(7);
        for (index, sr) in [22050.0, 44100.0, 48000.0, 96000.0f32].into_iter().cycle().take(40).enumerate() {
            let mut voice = random_voice(Style::ALL[index % Style::ALL.len()], &mut rng);
            voice[dx7::global::ALG] = (index % 32) as u8;
            let mut synth = Synth::new(sr);
            synth.set_firmware_va(index % 2 == 0);
            load(&mut synth, &voice, 0);
            for (n, key) in [36u8, 60, 67, 96].into_iter().enumerate() {
                synth.queue(1 + n as u64 * 500, &short(0x90, key, 40 + 20 * n as u8));
                synth.queue((sr * 0.2) as u64, &short(0x80, key, 0));
            }
            let out = render(&mut synth, 0.4);
            assert!(out.iter().all(|s| s.is_finite() && s.abs() <= 1.0), "voice {index}");
        }
    }
}
