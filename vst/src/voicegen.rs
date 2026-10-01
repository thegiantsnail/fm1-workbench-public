//! Randomize, mutate and morph, ported from the workbench app (`app.js`).

use crate::algo;
use crate::dx7::{self, global, op, op_index, Voice, EDIT_SIZE, VOICE_PARAMS};

/// Small deterministic generator (xorshift64*): no dependency, and tests can seed it.
pub struct Rng(u64);

impl Rng {
    pub fn new(seed: u64) -> Self {
        Rng(seed | 1)
    }

    fn next(&mut self) -> u64 {
        self.0 ^= self.0 >> 12;
        self.0 ^= self.0 << 25;
        self.0 ^= self.0 >> 27;
        self.0.wrapping_mul(0x2545_F491_4F6C_DD1D)
    }

    /// Uniform in 0.0..1.0.
    pub fn unit(&mut self) -> f64 {
        (self.next() >> 11) as f64 / (1u64 << 53) as f64
    }

    /// Uniform integer in `low..=high`.
    pub fn range(&mut self, low: i32, high: i32) -> i32 {
        low + (self.unit() * (high - low + 1) as f64) as i32
    }

    pub fn chance(&mut self, probability: f64) -> bool {
        self.unit() < probability
    }

    pub fn pick<T: Copy>(&mut self, items: &[T]) -> T {
        items[self.range(0, items.len() as i32 - 1) as usize]
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Style {
    Any,
    Keys,
    Bass,
    Pad,
    Bell,
    Pluck,
}

impl Style {
    pub const ALL: [Style; 6] =
        [Style::Any, Style::Keys, Style::Bass, Style::Pad, Style::Bell, Style::Pluck];

    pub fn label(self) -> &'static str {
        match self {
            Style::Any => "Any",
            Style::Keys => "Keys",
            Style::Bass => "Bass",
            Style::Pad => "Pad",
            Style::Bell => "Bell",
            Style::Pluck => "Pluck",
        }
    }

    fn name(self) -> &'static str {
        match self {
            Style::Any => "RANDOM",
            Style::Keys => "RND KEYS",
            Style::Bass => "RND BASS",
            Style::Pad => "RND PAD",
            Style::Bell => "RND BELL",
            Style::Pluck => "RND PLUCK",
        }
    }
}

pub fn random_voice(style: Style, rng: &mut Rng) -> Voice {
    let mut v = dx7::init_voice();
    let algorithms: &[u8] = match style {
        Style::Any => &[],
        Style::Keys => &[4, 5, 0, 1, 20, 21],
        Style::Bass => &[0, 1, 15, 16, 17],
        Style::Pad => &[4, 5, 21, 22, 30],
        Style::Bell => &[4, 5, 28, 29, 30],
        Style::Pluck => &[0, 1, 2, 4, 15],
    };
    let algorithm = if algorithms.is_empty() { rng.range(0, 31) as u8 } else { rng.pick(algorithms) };
    v[global::ALG] = algorithm;
    v[global::FB] = if style == Style::Bass { rng.range(3, 7) } else { rng.range(0, 6) } as u8;
    let carriers = algo::carriers(algorithm);
    let ratios: &[i32] = if style == Style::Bell {
        &[1, 2, 3, 3, 4, 5, 7, 9, 11, 14]
    } else {
        &[1, 1, 1, 2, 2, 3, 4, 0, 5, 6, 8]
    };
    for n in 1..=6 {
        let carrier = carriers[n - 1];
        let mut set = |field: usize, value: i32| v[op_index(n, field)] = value.clamp(0, 127) as u8;
        let coarse = if carrier {
            rng.pick(&[1, 1, 1, 2, if style == Style::Bass { 0 } else { 1 }])
        } else {
            rng.pick(ratios)
        };
        set(op::FC, coarse);
        let fine = if style == Style::Bell && rng.chance(0.4) {
            rng.range(0, 60)
        } else if rng.chance(0.15) {
            rng.range(0, 20)
        } else {
            0
        };
        set(op::FF, fine);
        set(op::DT, rng.range(4, 10));
        let level = if carrier {
            rng.range(88, 99)
        } else {
            rng.range(if style == Style::Pad { 40 } else { 55 }, 92)
        };
        set(op::OL, level);
        set(op::KVS, rng.range(1, 5));
        set(op::RS, rng.range(0, 4));
        let sustain = match style {
            Style::Keys => rng.range(0, 70),
            Style::Bass => rng.range(0, 80),
            Style::Pad => rng.range(80, 99),
            Style::Bell | Style::Pluck => 0,
            Style::Any => rng.range(0, 99),
        };
        set(op::R1, if style == Style::Pad { rng.range(30, 70) } else { rng.range(80, 99) });
        set(op::R2, if style == Style::Pluck { rng.range(55, 80) } else { rng.range(25, 80) });
        set(op::R3, rng.range(20, 70));
        let release = match style {
            Style::Pad => rng.range(30, 60),
            Style::Bell => rng.range(20, 45),
            _ => rng.range(45, 80),
        };
        set(op::R4, release);
        set(op::L1, 99);
        set(op::L2, if carrier { rng.range(sustain.max(60), 99) } else { rng.range(40, 99) });
        set(op::L3, if carrier { sustain } else { rng.range(0, 90) });
        set(op::L4, 0);
    }
    v[global::LFS] = rng.range(20, 45) as u8;
    v[global::LPMD] = if style == Style::Pad { rng.range(0, 8) } else { 0 } as u8;
    v[global::LPMS] = rng.range(1, 3) as u8;
    dx7::set_name(&mut v, &format!("{} {}", style.name(), rng.range(10, 99)));
    dx7::clamp(&mut v);
    v
}

/// Nudge a voice. `amount` is 0..=100: how far values may move and how likely the coarse
/// ratios and the algorithm are to change.
pub fn mutate(source: &Voice, amount: u8, rng: &mut Rng) -> Voice {
    let mut v = *source;
    let k = amount.min(100) as f64 / 100.0;
    let nudge = |value: u8, max: u8, rng: &mut Rng| {
        let moved = value as f64 + (rng.unit() * 2.0 - 1.0) * max as f64 * k;
        moved.round().clamp(0.0, max as f64) as u8
    };
    const NUDGED: [usize; 10] =
        [op::OL, op::R1, op::R2, op::R3, op::R4, op::L2, op::L3, op::FF, op::KVS, op::DT];
    for n in 1..=6 {
        for field in NUDGED {
            if rng.chance(0.5) {
                let index = op_index(n, field);
                v[index] = nudge(v[index], dx7::field_of(index).max, rng);
            }
        }
    }
    for n in 1..=6 {
        if rng.chance(k * 0.5) {
            let index = op_index(n, op::FC);
            v[index] = (v[index] as i32 + rng.pick(&[-1, 1])).clamp(0, 31) as u8;
        }
    }
    if rng.chance(0.5) {
        v[global::FB] = nudge(v[global::FB], 7, rng);
    }
    if rng.chance(k * 0.3) {
        v[global::ALG] = rng.range(0, 31) as u8;
    }
    dx7::clamp(&mut v);
    v
}

/// Switches and ratios cannot be blended; they jump from A to B at the midpoint.
fn snaps(index: usize) -> bool {
    matches!(dx7::field_of(index).key, "alg" | "oks" | "lfks" | "lfw" | "mode" | "fc" | "lc" | "rc")
}

/// Blend two voices; `t` is 0.0 (all A) to 1.0 (all B).
pub fn morph(a: &Voice, b: &Voice, t: f32) -> Voice {
    let t = t.clamp(0.0, 1.0);
    let mut v: Voice = [0; EDIT_SIZE];
    for index in 0..VOICE_PARAMS {
        v[index] = if snaps(index) {
            if t < 0.5 { a[index] } else { b[index] }
        } else {
            (a[index] as f32 + (b[index] as f32 - a[index] as f32) * t).round() as u8
        };
    }
    dx7::set_name(&mut v, &format!("MORPH {}", (t * 100.0).round() as u32));
    dx7::clamp(&mut v);
    v
}

#[cfg(test)]
mod tests {
    use super::*;

    fn in_range(v: &Voice) -> bool {
        (0..VOICE_PARAMS).all(|i| v[i] <= dx7::field_of(i).max)
    }

    #[test]
    fn random_voices_are_valid_audible_and_follow_their_style() {
        let mut rng = Rng::new(42);
        for style in Style::ALL {
            for _ in 0..200 {
                let v = random_voice(style, &mut rng);
                assert!(in_range(&v));
                let carriers = algo::carriers(v[global::ALG]);
                for n in 1..=6 {
                    if carriers[n - 1] {
                        assert!(v[op_index(n, op::OL)] >= 88, "carriers must be loud");
                    }
                }
                match style {
                    Style::Bass => assert!(v[global::FB] >= 3),
                    Style::Pad => assert!(v[op_index(1, op::R1)] <= 70),
                    Style::Bell => assert!([4, 5, 28, 29, 30].contains(&v[global::ALG])),
                    _ => {}
                }
                assert!(dx7::name_of(&v).starts_with(style.name()));
            }
        }
    }

    #[test]
    fn mutation_scales_with_amount_and_stays_valid() {
        let mut rng = Rng::new(7);
        let base = random_voice(Style::Keys, &mut rng);
        assert_eq!(mutate(&base, 0, &mut rng)[..VOICE_PARAMS], base[..VOICE_PARAMS]);
        let distance = |amount: u8, rng: &mut Rng| -> u32 {
            (0..50)
                .map(|_| {
                    let m = mutate(&base, amount, rng);
                    assert!(in_range(&m));
                    (0..VOICE_PARAMS).map(|i| (m[i] as i32 - base[i] as i32).unsigned_abs()).sum::<u32>()
                })
                .sum()
        };
        let (small, large) = (distance(5, &mut rng), distance(60, &mut rng));
        assert!(small > 0 && large > small * 4, "{small} vs {large}");
    }

    #[test]
    fn morph_blends_levels_and_snaps_switches_at_the_midpoint() {
        let mut a = dx7::init_voice();
        let mut b = dx7::init_voice();
        a[op_index(1, op::OL)] = 20;
        b[op_index(1, op::OL)] = 80;
        a[global::ALG] = 0;
        b[global::ALG] = 31;
        a[op_index(2, op::FC)] = 1;
        b[op_index(2, op::FC)] = 14;
        assert_eq!(morph(&a, &b, 0.0)[..VOICE_PARAMS], a[..VOICE_PARAMS]);
        assert_eq!(morph(&a, &b, 1.0)[..VOICE_PARAMS], b[..VOICE_PARAMS]);
        let quarter = morph(&a, &b, 0.25);
        assert_eq!((quarter[op_index(1, op::OL)], quarter[global::ALG]), (35, 0));
        assert_eq!(quarter[op_index(2, op::FC)], 1);
        let most = morph(&a, &b, 0.75);
        assert_eq!((most[op_index(1, op::OL)], most[global::ALG]), (65, 31));
        assert_eq!(dx7::name_of(&most), "MORPH 75");
    }
}
