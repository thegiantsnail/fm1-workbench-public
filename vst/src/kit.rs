//! Drum kits: each key plays its own voice, switched by sending parameter differences just before
//! the note. Sounding notes keep the voice they started with, so hits from different voices can
//! overlap. Includes the workbench's drum macros (`drums.js`).

use serde::{Deserialize, Serialize};

use crate::algo;
use crate::dx7::{self, global, op, op_index, Voice, EDIT_SIZE};

/// First key of a kit loaded from the library; tracks follow chromatically.
pub const BASE_KEY: u8 = 36;

/// Musical controls applied as offsets to a voice's own parameters; the voice is never modified.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct Macros {
    /// Carrier levels, about 0.75 dB per step.
    pub level: i16,
    /// Carrier decay rates R2/R3; positive rings longer.
    pub decay: i16,
    /// Carrier release rate R4; positive rings longer.
    pub release: i16,
    /// Modulator levels: FM brightness.
    pub tone: i16,
    /// Pitch envelope start level: positive drops into the note.
    pub punch: i16,
    /// Pitch sweep time; `None` (or negative in workbench files) keeps the voice's own.
    pub sweep: Option<i16>,
    /// Feedback.
    pub grit: i16,
    /// Carrier velocity sensitivity; `None` keeps the voice's own.
    #[serde(rename = "dyn")]
    pub dynamics: Option<i16>,
}

impl Macros {
    pub fn apply(&self, base: &Voice) -> Voice {
        let mut v = *base;
        let carriers = algo::carriers(v[global::ALG]);
        let shift = |value: u8, by: i16, low: i16| (value as i16 + by).clamp(low, 99) as u8;
        for n in 1..=6 {
            let carrier = carriers[n - 1];
            let level = op_index(n, op::OL);
            if v[level] > 0 {
                // Silent operators stay silent.
                if carrier && self.level != 0 {
                    v[level] = shift(v[level], self.level, 1);
                }
                if !carrier && self.tone != 0 {
                    v[level] = shift(v[level], self.tone, 0);
                }
            }
            // Length is set by the carriers; modulator envelopes shape tone and are left alone.
            if carrier {
                for field in [op::R2, op::R3] {
                    v[op_index(n, field)] = shift(v[op_index(n, field)], -self.decay, 0);
                }
                v[op_index(n, op::R4)] = shift(v[op_index(n, op::R4)], -self.release, 0);
                if let Some(dynamics) = self.dynamics.filter(|d| *d >= 0) {
                    v[op_index(n, op::KVS)] = dynamics.clamp(0, 7) as u8;
                }
            }
        }
        v[global::PL4] = shift(v[global::PL4], self.punch, 0);
        if let Some(sweep) = self.sweep.filter(|s| *s >= 0) {
            v[global::PR1] = (99 - sweep).clamp(0, 99) as u8;
        }
        v[global::FB] = (v[global::FB] as i16 + self.grit).clamp(0, 7) as u8;
        v
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Track {
    pub name: String,
    /// Incoming MIDI note that triggers this track.
    pub key: u8,
    /// Pitch played on the FM-1 (before the voice's transpose).
    pub note: u8,
    /// The voice's 155 parameters and name.
    pub voice: Vec<u8>,
    #[serde(default)]
    pub macros: Macros,
}

impl Track {
    pub fn new(name: &str, key: u8, note: u8, voice: &Voice) -> Self {
        Track { name: name.to_string(), key, note, voice: voice.to_vec(), macros: Macros::default() }
    }

    /// The stored voice as an edit buffer; short or out-of-range data is tolerated.
    pub fn base(&self) -> Voice {
        let mut voice = dx7::init_voice();
        let n = self.voice.len().min(EDIT_SIZE - 1);
        voice[..n].copy_from_slice(&self.voice[..n]);
        dx7::clamp(&mut voice);
        voice
    }

    pub fn voice_name(&self) -> String {
        dx7::name_of(&self.base())
    }
}

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct Kit {
    pub name: String,
    pub tracks: Vec<Track>,
}

#[derive(Clone, Copy)]
pub struct Slot {
    pub active: bool,
    pub note: u8,
    pub voice: Voice,
}

/// The kit as the audio thread uses it: one slot per incoming key, macros already applied.
#[derive(Clone)]
pub struct Table {
    pub slots: [Slot; 128],
}

impl Default for Table {
    fn default() -> Self {
        Table { slots: [Slot { active: false, note: 0, voice: [0; EDIT_SIZE] }; 128] }
    }
}

impl Kit {
    pub fn table(&self) -> Box<Table> {
        let mut table = Box::<Table>::default();
        for track in &self.tracks {
            // The first track listed for a key wins.
            let slot = &mut table.slots[(track.key & 0x7F) as usize];
            if !slot.active {
                *slot = Slot {
                    active: true,
                    note: track.note & 0x7F,
                    voice: track.macros.apply(&track.base()),
                };
            }
        }
        table
    }

    /// Lowest key from `BASE_KEY` up that no track uses yet.
    pub fn free_key(&self) -> u8 {
        (BASE_KEY..128)
            .chain(0..BASE_KEY)
            .find(|key| self.tracks.iter().all(|t| t.key != *key))
            .unwrap_or(BASE_KEY)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn drum() -> Voice {
        let mut v = dx7::init_voice();
        v[global::ALG] = 4; // algorithm 5: carriers 1, 3, 5
        for (n, level) in [(1, 90), (2, 70), (3, 0), (4, 60)] {
            v[op_index(n, op::OL)] = level;
        }
        v[op_index(1, op::R2)] = 50;
        v[op_index(2, op::R2)] = 50;
        v
    }

    #[test]
    fn macros_move_carriers_and_modulators_separately() {
        let base = drum();
        assert_eq!(Macros::default().apply(&base), base);
        let m = Macros { level: -16, tone: 10, decay: 20, grit: 3, punch: 30, ..Macros::default() };
        let v = m.apply(&base);
        assert_eq!(v[op_index(1, op::OL)], 74); // carrier: level
        assert_eq!(v[op_index(2, op::OL)], 80); // modulator: tone
        assert_eq!(v[op_index(3, op::OL)], 0); // silent carrier stays silent
        assert_eq!(v[op_index(1, op::R2)], 30); // longer decay = lower rate, carriers only
        assert_eq!(v[op_index(2, op::R2)], 50);
        assert_eq!((v[global::FB], v[global::PL4]), (3, 80));

        let extreme = Macros { level: -200, sweep: Some(120), dynamics: Some(9), ..m }.apply(&base);
        assert_eq!(extreme[op_index(1, op::OL)], 1); // never quite silenced
        assert_eq!((extreme[global::PR1], extreme[op_index(1, op::KVS)]), (0, 7));
        let own = Macros { sweep: Some(-1), dynamics: Some(-1), ..Macros::default() }.apply(&base);
        assert_eq!(own, base); // the workbench writes -1 for "keep the voice's own"
    }

    #[test]
    fn workbench_macro_json_reads_and_kit_round_trips() {
        let m: Macros = serde_json::from_str(r#"{"level": -16, "dyn": 3}"#).unwrap();
        assert_eq!((m.level, m.dynamics, m.sweep), (-16, Some(3), None));

        let mut kit = Kit { name: "Test".into(), tracks: vec![] };
        kit.tracks.push(Track::new("Kick", 36, 36, &drum()));
        kit.tracks.push(Track::new("Snare", 37, 50, &dx7::init_voice()));
        kit.tracks.push(Track::new("Shadowed", 36, 99, &dx7::init_voice()));
        kit.tracks[0].macros.level = -10;
        let back: Kit = serde_json::from_str(&serde_json::to_string(&kit).unwrap()).unwrap();
        assert_eq!(back, kit);

        let table = kit.table();
        assert!(table.slots[36].active && table.slots[37].active && !table.slots[38].active);
        assert_eq!((table.slots[36].note, table.slots[37].note), (36, 50));
        assert_eq!(table.slots[36].voice[op_index(1, op::OL)], 80);
        assert_eq!(kit.free_key(), 38);
        assert_eq!(kit.tracks[0].voice_name(), "INIT VOICE");
    }

    #[test]
    fn damaged_track_data_still_gives_a_valid_voice() {
        let short = Track { name: "x".into(), key: 40, note: 60, voice: vec![255; 30], macros: Macros::default() };
        let v = short.base();
        assert!((0..dx7::VOICE_PARAMS).all(|i| v[i] <= dx7::field_of(i).max));
        assert_eq!(v[dx7::OP_MASK], 63);
    }
}
