//! Host-visible parameters: the 145 DX7 voice parameters, the FM-1's 24 effect controllers, and
//! a few controls for the link itself.
//!
//! `Params` is implemented by hand rather than derived because the voice parameters are generated
//! from the table in `dx7`. Parameter ids are part of saved projects: never rename them.

use std::collections::BTreeMap;
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::{Arc, RwLock};

use nih_plug::params::persist::PersistentField;
use nih_plug::prelude::*;
use nih_plug_egui::EguiState;

use crate::dx7::{self, EDIT_SIZE, VOICE_PARAMS};
use crate::engine::FX_COUNT;
use crate::kit::{Kit, Table};
use crate::midi_out::Link;
use crate::speech;

pub const EDITOR_SIZE: (u32, u32) = (1060, 640);

/// State shared by the audio thread, the editor and saved projects that is not a host parameter.
#[derive(Default)]
pub struct Shared {
    pub link: Arc<Link>,
    /// The drum kit as edited and saved.
    kit: RwLock<Kit>,
    /// The same kit as the audio thread plays it; it copies this when `kit_generation` moves.
    pub table: RwLock<Box<Table>>,
    pub kit_generation: AtomicU32,
    /// Requests from the editor's buttons, taken by the audio thread.
    pub panic: AtomicBool,
    pub resend: AtomicBool,
    /// 0 for none, otherwise `AUDITION | kit flag | key << 8 | velocity`.
    pub audition: AtomicU32,
    pub speech: Arc<speech::Shared>,
    /// Counters the audio thread keeps for the status file: blocks processed, notes received.
    pub blocks: AtomicU32,
    pub notes: AtomicU32,
}

pub const AUDITION: u32 = 1 << 16;
pub const AUDITION_KIT: u32 = 1 << 17;

impl Shared {
    pub fn kit(&self) -> Kit {
        self.kit.read().map(|kit| kit.clone()).unwrap_or_default()
    }

    pub fn set_kit(&self, kit: Kit) {
        let table = kit.table();
        if let Ok(mut slot) = self.table.write() {
            *slot = table;
        }
        if let Ok(mut slot) = self.kit.write() {
            *slot = kit;
        }
        self.kit_generation.fetch_add(1, Ordering::Release);
    }

    /// Ask the audio thread to play a short note. `through_kit` plays the key's kit track even
    /// when kit mode is off.
    pub fn play(&self, key: u8, velocity: u8, through_kit: bool) {
        let kit = if through_kit { AUDITION_KIT } else { 0 };
        let request = AUDITION | kit | (key as u32 & 0x7F) << 8 | velocity as u32 & 0x7F;
        self.audition.store(request, Ordering::Release);
    }
}

const EFFECTS: [(&str, &str, [(&str, &str, i32, i32); 3]); 6] = [
    ("flt", "Filter", [("type", "Type", 2, 0), ("cut", "Cutoff", 107, 107), ("q", "Q", 10, 0)]),
    ("rev", "Reverb", [("type", "Type", 2, 0), ("decay", "Decay", 100, 50), ("mix", "Mix", 100, 30)]),
    ("dly", "Delay", [("decay", "Decay", 100, 50), ("rate", "Rate", 100, 50), ("mix", "Mix", 100, 30)]),
    ("dst", "Distortion", [("gain", "Gain", 100, 0), ("tone", "Tone", 100, 50), ("level", "Level", 100, 50)]),
    ("cho", "Chorus", [("freq", "Freq", 100, 50), ("depth", "Depth", 100, 50), ("mix", "Mix", 100, 30)]),
    ("pha", "Phaser", [("freq", "Freq", 100, 50), ("depth", "Depth", 100, 50), ("mix", "Mix", 100, 30)]),
];

pub enum Fx {
    Switch(BoolParam),
    Value(IntParam),
}

pub struct Fm1Params {
    /// Voice parameters in VCED order (index = DX7 parameter number).
    pub voice: Vec<IntParam>,
    /// Effect controllers in CC order (CC 0..23 on the effect channel).
    pub fx: Vec<Fx>,
    /// Off by default: the plugin then leaves the unit's own effect settings alone.
    pub control_fx: BoolParam,
    pub key_channel: IntParam,
    pub fx_channel: IntParam,
    /// Turning either of these on triggers the action once; turn it off to arm it again.
    pub panic: BoolParam,
    pub resend: BoolParam,
    /// On: each key plays its kit track's voice instead of the voice parameters above.
    pub kit_mode: BoolParam,
    /// Kit hits are delayed by this long so each voice change reaches the unit before its note.
    pub kit_lead: IntParam,
    /// On when the unit runs Baud Girl's FM-1+VA firmware: LFO speed/delay and volume then go
    /// out as controllers, which stock firmware ignores.
    pub firmware_va: BoolParam,
    /// Master volume (CC 7), FM-1+VA only.
    pub volume: IntParam,
    /// On: a note speaks the phrase at that note's pitch instead of playing the voice.
    pub speech_mode: BoolParam,
    /// Voice name (10 characters on the device), saved with the project.
    pub name: RwLock<String>,
    pub shared: Arc<Shared>,
    pub editor_state: Arc<EguiState>,
}

fn names(table: &'static [&'static str], offset: i32) -> Arc<dyn Fn(i32) -> String + Send + Sync> {
    Arc::new(move |value| match table.get(value as usize) {
        Some(name) => name.to_string(),
        None => (value + offset).to_string(),
    })
}

fn voice_param(index: usize, default: u8) -> IntParam {
    let field = dx7::field_of(index);
    let label = match dx7::op_of(index) {
        Some(op) => format!("OP{op} {}", field.label),
        None => field.label.to_string(),
    };
    let range = IntRange::Linear { min: 0, max: field.max as i32 };
    let param = IntParam::new(label, default as i32, range);
    match field.key {
        "alg" => param.with_value_to_string(names(&[], 1)), // shown 1..32 as on a DX7
        "trnp" => param.with_value_to_string(names(&[], -24)).with_unit(" st"),
        "dt" => param.with_value_to_string(names(&[], -7)),
        "mode" => param.with_value_to_string(names(&["Ratio", "Fixed"], 0)),
        "oks" | "lfks" => param.with_value_to_string(names(&["Off", "On"], 0)),
        "lc" | "rc" => param.with_value_to_string(names(&["-Lin", "-Exp", "+Exp", "+Lin"], 0)),
        "lfw" => param.with_value_to_string(names(
            &["Triangle", "Saw Down", "Saw Up", "Square", "Sine", "S&H"],
            0,
        )),
        _ => param,
    }
}

impl Default for Fm1Params {
    fn default() -> Self {
        let mut fx = Vec::with_capacity(FX_COUNT);
        for (_, effect, values) in EFFECTS {
            fx.push(Fx::Switch(BoolParam::new(format!("{effect} On"), false)));
            for (_, label, max, default) in values {
                let range = IntRange::Linear { min: 0, max };
                let param = IntParam::new(format!("{effect} {label}"), default, range);
                fx.push(Fx::Value(match (effect, label) {
                    ("Filter", "Type") => param.with_value_to_string(names(&["LP", "BP", "HP"], 0)),
                    ("Reverb", "Type") => param.with_value_to_string(names(&["Room", "Hall", "Plate"], 0)),
                    _ => param,
                }));
            }
        }
        let channel = |name: &str, default| {
            IntParam::new(name, default, IntRange::Linear { min: 1, max: 16 }).non_automatable()
        };
        let init = dx7::init_voice(); // operator 1 audible, unlike a raw field default
        Fm1Params {
            voice: (0..VOICE_PARAMS).map(|index| voice_param(index, init[index])).collect(),
            fx,
            control_fx: BoolParam::new("Control Effects", false),
            key_channel: channel("Note Channel", 1),
            fx_channel: channel("Effect Channel", 2),
            panic: BoolParam::new("Panic", false),
            resend: BoolParam::new("Resend Voice", false),
            kit_mode: BoolParam::new("Kit Mode", false),
            kit_lead: IntParam::new("Kit Lookahead", 20, IntRange::Linear { min: 0, max: 40 })
                .with_unit(" ms")
                .non_automatable(),
            firmware_va: BoolParam::new("FM-1+VA Firmware", false).non_automatable(),
            volume: IntParam::new("Volume", 100, IntRange::Linear { min: 0, max: 127 }),
            speech_mode: BoolParam::new("Speech Mode", false),
            name: RwLock::new("INIT VOICE".into()),
            shared: Arc::new(Shared::default()),
            editor_state: EguiState::from_size(EDITOR_SIZE.0, EDITOR_SIZE.1),
        }
    }
}

impl Fm1Params {
    /// The edit buffer the device should hold.
    pub fn edit_buffer(&self, name: &[u8; dx7::NAME_LEN]) -> [u8; EDIT_SIZE] {
        let mut voice = [0u8; EDIT_SIZE];
        for (slot, param) in voice.iter_mut().zip(&self.voice) {
            *slot = param.value() as u8;
        }
        voice[dx7::NAME_START..dx7::NAME_START + dx7::NAME_LEN].copy_from_slice(name);
        voice[dx7::OP_MASK] = 63;
        voice
    }

    pub fn fx_values(&self) -> [u8; FX_COUNT] {
        let mut values = [0u8; FX_COUNT];
        for (slot, param) in values.iter_mut().zip(&self.fx) {
            *slot = match param {
                Fx::Switch(p) => p.value() as u8,
                Fx::Value(p) => p.value() as u8,
            };
        }
        values
    }

    /// Voice parameters and name as the editor sees them.
    pub fn current_voice(&self) -> dx7::Voice {
        let mut name = *b"          ";
        if let Ok(text) = self.name.read() {
            let mut voice = [0u8; EDIT_SIZE];
            dx7::set_name(&mut voice, &text);
            name.copy_from_slice(&voice[dx7::NAME_START..dx7::NAME_START + dx7::NAME_LEN]);
        }
        self.edit_buffer(&name)
    }

    /// Compile `settings` as the phrase to speak, on the channels currently chosen.
    pub fn request_speech(&self, settings: speech::Settings) {
        let (ch, fx_ch) = (self.key_channel.value() - 1, self.fx_channel.value() - 1);
        self.shared.speech.request(settings, ch as u8, fx_ch as u8);
    }

    pub fn set_name(&self, text: &str) {
        if let Ok(mut name) = self.name.write() {
            *name = clean_name(text);
        }
    }

    /// The saved name as the ten bytes the device stores.
    pub fn name_bytes(&self) -> Option<[u8; dx7::NAME_LEN]> {
        // try_read: this is called from the audio thread, which must not wait on the editor.
        let name = self.name.try_read().ok()?;
        let mut voice = [0u8; EDIT_SIZE];
        dx7::set_name(&mut voice, &name);
        voice[dx7::NAME_START..dx7::NAME_START + dx7::NAME_LEN].try_into().ok()
    }
}

fn clean_name(text: &str) -> String {
    text.chars().filter(|c| c.is_ascii() && !c.is_ascii_control()).take(dx7::NAME_LEN).collect()
}

// SAFETY: every `ParamPtr` points into a `Vec` or field owned by `self`, which the wrapper keeps
// alive in an `Arc` and which is never resized or moved after construction.
unsafe impl Params for Fm1Params {
    fn param_map(&self) -> Vec<(String, ParamPtr, String)> {
        let mut map = Vec::with_capacity(VOICE_PARAMS + FX_COUNT + 10);
        // Hosts list parameters in this order: operators 1..6, then the voice globals.
        for op in 1..=6 {
            for (f, field) in dx7::OP_FIELDS.iter().enumerate() {
                let param = &self.voice[dx7::op_index(op, f)];
                map.push((format!("op{op}_{}", field.key), param.as_ptr(), format!("Operator {op}")));
            }
        }
        for (f, field) in dx7::GLOBAL_FIELDS.iter().enumerate() {
            let param = &self.voice[dx7::global_index(f)];
            map.push((field.key.to_string(), param.as_ptr(), "Voice".to_string()));
        }
        let mut fx = self.fx.iter();
        for (prefix, effect, values) in EFFECTS {
            let group = format!("Effects/{effect}");
            for key in std::iter::once("on").chain(values.iter().map(|v| v.0)) {
                let ptr = match fx.next() {
                    Some(Fx::Switch(p)) => p.as_ptr(),
                    Some(Fx::Value(p)) => p.as_ptr(),
                    None => unreachable!("EFFECTS and self.fx are built from the same table"),
                };
                map.push((format!("{prefix}_{key}"), ptr, group.clone()));
            }
        }
        for (id, ptr) in [
            ("fx_control", self.control_fx.as_ptr()),
            ("key_ch", self.key_channel.as_ptr()),
            ("fx_ch", self.fx_channel.as_ptr()),
            ("panic", self.panic.as_ptr()),
            ("resend", self.resend.as_ptr()),
            ("kit_mode", self.kit_mode.as_ptr()),
            ("kit_lead", self.kit_lead.as_ptr()),
            ("fw_va", self.firmware_va.as_ptr()),
            ("volume", self.volume.as_ptr()),
            ("speech_mode", self.speech_mode.as_ptr()),
        ] {
            map.push((id.to_string(), ptr, "FM-1".to_string()));
        }
        map
    }

    fn serialize_fields(&self) -> BTreeMap<String, String> {
        let mut fields = BTreeMap::new();
        let name = self.name.read().map(|n| n.clone()).unwrap_or_default();
        fields.insert("voice_name".to_string(), name);
        if let Ok(kit) = serde_json::to_string(&self.shared.kit()) {
            fields.insert("kit".to_string(), kit);
        }
        if let Ok(speech) = serde_json::to_string(&self.shared.speech.settings()) {
            fields.insert("speech".to_string(), speech);
        }
        if let Ok(state) = self.editor_state.map(|state| serde_json::to_string(state)) {
            fields.insert("editor-state".to_string(), state);
        }
        fields
    }

    fn deserialize_fields(&self, serialized: &BTreeMap<String, String>) {
        if let Some(saved) = serialized.get("voice_name") {
            self.set_name(saved);
        }
        // A project saved before kits existed has no kit: that must clear one left from before.
        let kit = serialized.get("kit").and_then(|text| serde_json::from_str::<Kit>(text).ok());
        self.shared.set_kit(kit.unwrap_or_default());
        // Compile the saved phrase now, so that notes can speak it without the editor open. With
        // no phrase the speech engine is not started at all.
        let saved = serialized.get("speech").and_then(|text| serde_json::from_str(text).ok());
        let speech: speech::Settings = saved.unwrap_or_default();
        if !speech.text.trim().is_empty() || self.shared.speech.status().state != speech::State::Idle {
            self.request_speech(speech);
        }
        if let Some(Ok(state)) = serialized.get("editor-state").map(|s| serde_json::from_str(s)) {
            self.editor_state.set(state);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn defaults_are_the_init_voice_and_ids_are_unique_and_stable() {
        let params = Fm1Params::default();
        assert_eq!(params.edit_buffer(&params.name_bytes().unwrap()), dx7::init_voice());

        let map = params.param_map();
        assert_eq!(map.len(), VOICE_PARAMS + FX_COUNT + 10);
        let mut ids: Vec<_> = map.iter().map(|(id, _, _)| id.as_str()).collect();
        assert_eq!(&ids[..3], ["op1_r1", "op1_r2", "op1_r3"]);
        assert_eq!(ids[6 * 21 + 8], "alg");
        assert_eq!(ids[VOICE_PARAMS], "flt_on");
        assert_eq!(ids[VOICE_PARAMS + 15], "dst_level"); // CC 15, the usable output trim
        assert_eq!(map[16].2, "Operator 1");
        ids.sort_unstable();
        ids.dedup();
        assert_eq!(ids.len(), VOICE_PARAMS + FX_COUNT + 10);
    }

    #[test]
    fn effect_values_follow_cc_order_and_the_name_round_trips() {
        let params = Fm1Params::default();
        let fx = params.fx_values();
        assert_eq!((fx[0], fx[2], fx[3], fx[12], fx[15]), (0, 107, 0, 0, 50));

        params.deserialize_fields(&BTreeMap::from([(
            "voice_name".to_string(),
            "E.PIANO 1\u{7}é and more".to_string(),
        )]));
        assert_eq!(params.serialize_fields()["voice_name"], "E.PIANO 1 ");
        assert_eq!(&params.name_bytes().unwrap(), b"E.PIANO 1 ");
        assert_eq!(dx7::name_of(&params.current_voice()), "E.PIANO 1");
    }

    #[test]
    fn kit_is_saved_with_the_project_and_reaches_the_audio_table() {
        use crate::kit::Track;

        let saved = Fm1Params::default();
        let mut kit = Kit { name: "Live".into(), tracks: vec![] };
        kit.tracks.push(Track::new("Kick", 36, 40, &dx7::init_voice()));
        kit.tracks[0].macros.level = -9;
        saved.shared.set_kit(kit.clone());
        let fields = saved.serialize_fields();

        let loaded = Fm1Params::default();
        loaded.deserialize_fields(&fields);
        assert_eq!(loaded.shared.kit(), kit);
        assert_eq!(loaded.shared.kit_generation.load(Ordering::Acquire), 1);
        let table = loaded.shared.table.read().unwrap();
        assert!(table.slots[36].active && table.slots[36].note == 40);
        assert_eq!(table.slots[36].voice[dx7::op_index(1, 16)], 90);
        drop(table);

        // Loading a project saved without a kit removes the one in memory.
        loaded.deserialize_fields(&BTreeMap::from([("voice_name".to_string(), "X".to_string())]));
        assert!(loaded.shared.kit().tracks.is_empty());
        assert!(!loaded.shared.table.read().unwrap().slots[36].active);

        loaded.shared.play(60, 100, true);
        let request = loaded.shared.audition.load(Ordering::Acquire);
        assert_eq!(request, AUDITION | AUDITION_KIT | 60 << 8 | 100);
    }
}
