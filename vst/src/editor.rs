//! The editor window: voice library, voice editor, generators, effects and drum kit.
//!
//! Everything a control changes goes through the host (`ParamSetter`), so edits are automatable,
//! undoable in the host and saved with the project. The audio thread then sends the differences
//! to the FM-1; nothing here talks to the device.

use std::sync::atomic::Ordering;
use std::sync::{Arc, Mutex};

use nih_plug::prelude::*;
use nih_plug_egui::egui::{self, Color32, RichText};
use nih_plug_egui::create_egui_editor;

use crate::algo;
use crate::dx7::{self, Voice};
use crate::kit::{Kit, Track};
use crate::library::{self, Filter, Library, State, TAGS};
use crate::params::{Fm1Params, Fx, Sound};
use crate::speech;
use crate::voicegen::{self, Rng, Style};

const UNDO_DEPTH: usize = 32;
/// Key and velocity of the note the audition buttons play on the main voice (C5 in FL Studio).
const AUDITION_KEY: u8 = 60;
const AUDITION_VELOCITY: u8 = 100;
const GOOD: Color32 = Color32::from_rgb(110, 200, 120);
const BAD: Color32 = Color32::from_rgb(230, 110, 100);
const NOTE_NAMES: [&str; 12] = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];

#[derive(Clone, Copy, PartialEq, Eq)]
enum Tab {
    Voice,
    Generate,
    Effects,
    Kit,
    Speech,
}

struct Gui {
    params: Arc<Fm1Params>,
    library: Arc<Mutex<State>>,
    loaded: Option<Arc<Library>>,
    load_error: Option<String>,
    query: String,
    filter: Filter,
    /// The query and filter `results` was computed for; `None` forces a new search.
    searched: Option<(String, Filter)>,
    results: Vec<u32>,
    selected: Option<u32>,
    tab: Tab,
    undo: Vec<Voice>,
    style: Style,
    mutate_amount: u8,
    rng: Rng,
    morph_a: Option<Voice>,
    morph_b: Option<Voice>,
    morph_t: f32,
    kit_choice: usize,
    kit_track: Option<usize>,
    audition_on_load: bool,
    name_edit: String,
    /// The Speech tab's working copy; `None` until the tab is first shown.
    speech: Option<speech::Settings>,
}

pub fn create(params: Arc<Fm1Params>) -> Option<Box<dyn Editor>> {
    let seed = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0x9E37_79B9, |d| d.as_nanos() as u64);
    let gui = Gui {
        params: params.clone(),
        library: library::shared(),
        loaded: None,
        load_error: None,
        query: String::new(),
        filter: Filter::All,
        searched: None,
        results: Vec::new(),
        selected: None,
        tab: Tab::Voice,
        undo: Vec::new(),
        style: Style::Any,
        mutate_amount: 15,
        rng: Rng::new(seed),
        morph_a: None,
        morph_b: None,
        morph_t: 0.0,
        kit_choice: 0,
        kit_track: None,
        audition_on_load: true,
        name_edit: String::new(),
        speech: None,
    };
    create_egui_editor(params.editor_state.clone(), gui, |_, _| {}, |ctx, setter, gui| gui.show(ctx, setter))
}

/// FL Studio's name for a MIDI note (60 is C5 there).
pub fn note_name(note: u8) -> String {
    format!("{}{}", NOTE_NAMES[(note % 12) as usize], note / 12)
}

fn set_int(setter: &ParamSetter, param: &IntParam, value: i32) {
    if param.value() != value {
        setter.begin_set_parameter(param);
        setter.set_parameter(param, value);
        setter.end_set_parameter(param);
    }
}

fn set_bool(setter: &ParamSetter, param: &BoolParam, value: bool) {
    if param.value() != value {
        setter.begin_set_parameter(param);
        setter.set_parameter(param, value);
        setter.end_set_parameter(param);
    }
}

/// A draggable number bound to a host parameter, shown with the parameter's own formatting.
fn drag(ui: &mut egui::Ui, setter: &ParamSetter, param: &IntParam, max: i32) -> egui::Response {
    let mut value = param.value();
    let widget = egui::DragValue::new(&mut value).range(0..=max).speed(0.25).custom_formatter(|n, _| {
        param.normalized_value_to_string(param.preview_normalized(n as i32), true)
    });
    let response = ui.add(widget).on_hover_text(param.name());
    if response.drag_started() {
        setter.begin_set_parameter(param);
    }
    if response.changed() {
        if response.dragged() {
            setter.set_parameter(param, value);
        } else {
            set_int(setter, param, value); // typed in: one complete gesture
        }
    }
    if response.drag_stopped() {
        setter.end_set_parameter(param);
    }
    response
}

fn toggle(ui: &mut egui::Ui, setter: &ParamSetter, param: &BoolParam, label: &str) {
    let mut on = param.value();
    if ui.checkbox(&mut on, label).changed() {
        set_bool(setter, param, on);
    }
}

impl Gui {
    /// Make `voice` the plugin's voice. The host sees every changed parameter.
    fn apply(&mut self, setter: &ParamSetter, voice: &Voice) {
        for (index, param) in self.params.voice.iter().enumerate() {
            set_int(setter, param, voice[index] as i32);
        }
        self.params.set_name(&dx7::name_of(voice));
    }

    fn apply_with_undo(&mut self, setter: &ParamSetter, voice: &Voice) {
        self.remember();
        self.apply(setter, voice);
    }

    fn remember(&mut self) {
        if self.undo.len() == UNDO_DEPTH {
            self.undo.remove(0);
        }
        self.undo.push(self.params.current_voice());
    }

    fn audition(&self) {
        self.params.shared.play(AUDITION_KEY, AUDITION_VELOCITY, false);
    }

    fn poll_library(&mut self) {
        if self.loaded.is_some() || self.load_error.is_some() {
            return;
        }
        if let Ok(state) = self.library.try_lock() {
            match &*state {
                State::Loading => {}
                State::Ready(library) => {
                    self.loaded = Some(library.clone());
                    self.searched = None;
                }
                State::Failed(error) => self.load_error = Some(error.clone()),
            }
        }
    }

    fn show(&mut self, ctx: &egui::Context, setter: &ParamSetter) {
        self.poll_library();
        self.top_bar(ctx, setter);
        self.library_panel(ctx, setter);
        egui::CentralPanel::default().show(ctx, |ui| {
            ui.horizontal(|ui| {
                for (tab, label) in [
                    (Tab::Voice, "Voice"),
                    (Tab::Generate, "Generate"),
                    (Tab::Effects, "Effects"),
                    (Tab::Kit, "Drum kit"),
                    (Tab::Speech, "Speech"),
                ] {
                    ui.selectable_value(&mut self.tab, tab, label);
                }
            });
            ui.separator();
            egui::ScrollArea::vertical().auto_shrink([false, false]).show(ui, |ui| match self.tab {
                Tab::Voice => self.voice_tab(ui, setter),
                Tab::Generate => self.generate_tab(ui, setter),
                Tab::Effects => self.effects_tab(ui, setter),
                Tab::Kit => self.kit_tab(ui, setter),
                Tab::Speech => self.speech_tab(ui, setter),
            });
        });
    }

    fn top_bar(&mut self, ctx: &egui::Context, setter: &ParamSetter) {
        egui::TopBottomPanel::top("status").show(ctx, |ui| {
            ui.add_space(2.0);
            ui.horizontal(|ui| {
                let shared = self.params.shared.clone();
                let connected = shared.link.connected.load(Ordering::Relaxed);
                let (good, playing) = sound_status(self.params.sound_mode(), connected);
                ui.colored_label(if good { GOOD } else { BAD }, playing);
                let mut sound = self.params.sound.value();
                egui::ComboBox::from_id_salt("sound")
                    .width(96.0)
                    .selected_text(Sound::NAMES[sound.clamp(0, 3) as usize])
                    .show_ui(ui, |ui| {
                        for (index, name) in Sound::NAMES.iter().enumerate() {
                            ui.selectable_value(&mut sound, index as i32, *name);
                        }
                    })
                    .response
                    .on_hover_text(
                        "What makes the sound. Auto: the FM-1 when it is connected, otherwise the built-in software FM-1. Offline renders always use the built-in synth.",
                    );
                set_int(setter, &self.params.sound, sound);
                ui.separator();
                ui.label("Voice");
                let name = egui::TextEdit::singleline(&mut self.name_edit).char_limit(10).desired_width(96.0);
                let response = ui.add(name);
                if response.changed() {
                    self.params.set_name(&self.name_edit);
                } else if !response.has_focus() {
                    self.name_edit = dx7::name_of(&self.params.current_voice());
                }
                if ui.button("Init").on_hover_text("Reset to the DX7 init voice").clicked() {
                    self.apply_with_undo(setter, &dx7::init_voice());
                }
                let can_undo = !self.undo.is_empty();
                if ui.add_enabled(can_undo, egui::Button::new("Undo")).clicked() {
                    if let Some(voice) = self.undo.pop() {
                        self.apply(setter, &voice);
                    }
                }
                ui.separator();
                if ui.button("Play").on_hover_text("Play a short note").clicked() {
                    self.audition();
                }
                ui.checkbox(&mut self.audition_on_load, "Play on load");
                ui.separator();
                if ui.button("Resend").on_hover_text("Send the whole voice to the FM-1 again").clicked() {
                    shared.resend.store(true, Ordering::Relaxed);
                }
                if ui.button("Panic").on_hover_text("Note-off for every key").clicked() {
                    shared.panic.store(true, Ordering::Relaxed);
                }
                if self.params.speech_mode.value() {
                    ui.separator();
                    if shared.speech.available.load(Ordering::Relaxed) {
                        ui.colored_label(GOOD, "Speech mode");
                    } else {
                        ui.colored_label(BAD, "Speech mode: no phrase yet, notes play normally");
                    }
                } else if self.params.kit_mode.value() {
                    ui.separator();
                    ui.colored_label(GOOD, "Kit mode");
                }
            });
            ui.add_space(2.0);
        });
    }

    fn library_panel(&mut self, ctx: &egui::Context, setter: &ParamSetter) {
        egui::SidePanel::left("library").resizable(false).exact_width(290.0).show(ctx, |ui| {
            ui.add_space(4.0);
            let Some(library) = self.loaded.clone() else {
                match &self.load_error {
                    Some(error) => {
                        ui.colored_label(BAD, "Voice library not loaded");
                        ui.label(error);
                    }
                    None => {
                        ui.label("Loading the voice library...");
                    }
                }
                return;
            };
            ui.add(
                egui::TextEdit::singleline(&mut self.query)
                    .hint_text("Search name or bank")
                    .desired_width(f32::INFINITY),
            );
            ui.horizontal(|ui| {
                let label = |filter: Filter| match filter {
                    Filter::All => "All categories".to_string(),
                    Filter::Drums => "Drums".to_string(),
                    Filter::Tag(tag) => TAGS[tag].to_string(),
                };
                egui::ComboBox::from_id_salt("category").selected_text(label(self.filter)).show_ui(ui, |ui| {
                    let tags = (0..TAGS.len()).map(Filter::Tag);
                    for filter in [Filter::All, Filter::Drums].into_iter().chain(tags) {
                        ui.selectable_value(&mut self.filter, filter, label(filter));
                    }
                });
                ui.label(format!("{} of {}", self.results.len(), library.voices.len()));
            });
            let wanted = (self.query.clone(), self.filter);
            if self.searched.as_ref() != Some(&wanted) {
                library.search(&self.query, self.filter, &mut self.results);
                self.searched = Some(wanted);
            }
            let target = self.kit_target();
            match &target {
                Some((_, name)) => ui.colored_label(GOOD, format!("Click loads into kit track: {name}")),
                None => ui.weak("Click a voice to load it"),
            };
            ui.separator();

            let row_height = ui.spacing().interact_size.y;
            let mut clicked = None;
            egui::ScrollArea::vertical().auto_shrink([false, false]).show_rows(
                ui,
                row_height,
                self.results.len(),
                |ui, rows| {
                    for row in rows {
                        let index = self.results[row];
                        let entry = &library.voices[index as usize];
                        let slot = entry.slot.map_or(String::new(), |s| format!(" #{}", s + 1));
                        let detail = match entry.first_tag() {
                            Some(tag) => format!("{tag} · {}{slot}", library.bank_name(entry)),
                            None => format!("{}{slot}", library.bank_name(entry)),
                        };
                        let text = format!("{:<10}   {detail}", entry.name());
                        let label = egui::SelectableLabel::new(
                            self.selected == Some(index),
                            RichText::new(text).monospace(),
                        );
                        if ui.add(label).clicked() {
                            clicked = Some(index);
                        }
                    }
                },
            );
            if let Some(index) = clicked {
                self.selected = Some(index);
                let voice = library.voices[index as usize].voice();
                match target {
                    Some((track, _)) => self.load_into_track(track, &voice),
                    None => {
                        self.apply_with_undo(setter, &voice);
                        if self.audition_on_load {
                            self.audition();
                        }
                    }
                }
            }
        });
    }

    /// The kit track a library click loads into: only while the kit tab is showing a selection.
    fn kit_target(&self) -> Option<(usize, String)> {
        let track = self.kit_track.filter(|_| self.tab == Tab::Kit)?;
        let kit = self.params.shared.kit();
        kit.tracks.get(track).map(|t| (track, t.name.clone()))
    }

    fn load_into_track(&mut self, track: usize, voice: &Voice) {
        let mut kit = self.params.shared.kit();
        if let Some(target) = kit.tracks.get_mut(track) {
            target.voice = voice.to_vec();
            let key = target.key;
            self.params.shared.set_kit(kit);
            if self.audition_on_load {
                self.params.shared.play(key, AUDITION_VELOCITY, true);
            }
        }
    }

    fn voice_tab(&mut self, ui: &mut egui::Ui, setter: &ParamSetter) {
        let params = self.params.clone();
        let algorithm = params.voice[dx7::global::ALG].value() as u8;
        let carriers = algo::carriers(algorithm);
        ui.horizontal(|ui| {
            ui.strong("Algorithm");
            drag(ui, setter, &params.voice[dx7::global::ALG], 31);
            let heard: Vec<String> =
                (1..=6).filter(|n| carriers[n - 1]).map(|n| format!("OP{n}")).collect();
            let routes: Vec<String> = algo::edges(algorithm).map(|(f, t)| format!("{f}>{t}")).collect();
            ui.label(format!(
                "heard: {}   modulation: {}   feedback: OP{}",
                heard.join(" "),
                if routes.is_empty() { "none".to_string() } else { routes.join("  ") },
                algo::feedback_op(algorithm)
            ));
        });
        ui.add_space(6.0);

        ui.horizontal_top(|ui| {
            egui::Grid::new("operators").num_columns(7).striped(true).min_col_width(52.0).spacing([12.0, 3.0]).show(
                ui,
                |ui| {
                    ui.label("");
                    for n in 1..=6 {
                        let (role, color) = if carriers[n - 1] { ("carrier", GOOD) } else { ("mod", Color32::GRAY) };
                        ui.vertical(|ui| {
                            ui.strong(format!("OP{n}"));
                            ui.colored_label(color, role);
                        });
                    }
                    ui.end_row();
                    for (field, meta) in dx7::OP_FIELDS.iter().enumerate() {
                        ui.label(meta.label);
                        for n in 1..=6 {
                            drag(ui, setter, &params.voice[dx7::op_index(n, field)], meta.max as i32);
                        }
                        ui.end_row();
                    }
                },
            );
            ui.add_space(12.0);
            ui.separator();
            ui.add_space(12.0);
            egui::Grid::new("globals").num_columns(2).striped(true).spacing([12.0, 3.0]).show(ui, |ui| {
                ui.strong("Voice");
                ui.label("");
                ui.end_row();
                for (field, meta) in dx7::GLOBAL_FIELDS.iter().enumerate().filter(|(_, f)| f.key != "alg") {
                    ui.label(meta.label);
                    drag(ui, setter, &params.voice[dx7::global_index(field)], meta.max as i32);
                    ui.end_row();
                }
            });
        });
    }

    fn generate_tab(&mut self, ui: &mut egui::Ui, setter: &ParamSetter) {
        ui.heading("Random voice");
        ui.horizontal(|ui| {
            egui::ComboBox::from_id_salt("style").selected_text(self.style.label()).show_ui(ui, |ui| {
                for style in Style::ALL {
                    ui.selectable_value(&mut self.style, style, style.label());
                }
            });
            if ui.button("Randomize").clicked() {
                let voice = voicegen::random_voice(self.style, &mut self.rng);
                self.apply_with_undo(setter, &voice);
                self.audition();
            }
        });
        ui.add_space(10.0);

        ui.heading("Mutation");
        ui.horizontal(|ui| {
            ui.add(egui::Slider::new(&mut self.mutate_amount, 1..=100).text("amount"));
            if ui.button("Mutate").clicked() {
                let voice = voicegen::mutate(&self.params.current_voice(), self.mutate_amount, &mut self.rng);
                let name = dx7::name_of(&self.params.current_voice());
                let mut voice = voice;
                dx7::set_name(&mut voice, &name);
                self.apply_with_undo(setter, &voice);
                self.audition();
            }
        });
        ui.label("Small amounts nudge levels and envelopes; large ones also change ratios and the algorithm.");
        ui.add_space(10.0);

        ui.heading("Morph between two voices");
        ui.horizontal(|ui| {
            if ui.button("Set A = current").clicked() {
                self.morph_a = Some(self.params.current_voice());
            }
            ui.label(self.morph_a.as_ref().map_or("(none)".to_string(), dx7::name_of));
            ui.separator();
            if ui.button("Set B = current").clicked() {
                self.morph_b = Some(self.params.current_voice());
            }
            ui.label(self.morph_b.as_ref().map_or("(none)".to_string(), dx7::name_of));
        });
        let ready = self.morph_a.is_some() && self.morph_b.is_some();
        let slider = egui::Slider::new(&mut self.morph_t, 0.0..=1.0).text("A to B").show_value(true);
        let response = ui.add_enabled(ready, slider);
        if response.drag_started() {
            self.remember();
        }
        if response.changed() {
            if let (Some(a), Some(b)) = (self.morph_a, self.morph_b) {
                self.apply(setter, &voicegen::morph(&a, &b, self.morph_t));
            }
        }
        if response.drag_stopped() {
            self.audition();
        }
        ui.label("Levels and envelopes blend; the algorithm, ratios and switches change at the midpoint.");
    }

    fn effects_tab(&mut self, ui: &mut egui::Ui, setter: &ParamSetter) {
        let params = self.params.clone();
        toggle(ui, setter, &params.control_fx, "Control the FM-1's effects from this plugin");
        ui.label(
            "Off: the unit keeps its own effect settings. On: the values below are sent and saved with the project. The built-in synth has its own versions of these effects, heard only while this is on.",
        );
        ui.add_space(8.0);
        let enabled = params.control_fx.value();
        ui.add_enabled_ui(enabled, |ui| {
            egui::Grid::new("fx").num_columns(7).striped(true).spacing([12.0, 6.0]).show(ui, |ui| {
                for effect in params.fx.chunks(4) {
                    for control in effect {
                        match control {
                            Fx::Switch(param) => {
                                let name = param.name().trim_end_matches(" On").to_string();
                                toggle(ui, setter, param, &name);
                            }
                            Fx::Value(param) => {
                                let word = param.name().rsplit(' ').next().unwrap_or("").to_string();
                                ui.label(word);
                                let max = param.preview_plain(1.0);
                                drag(ui, setter, param, max);
                            }
                        }
                    }
                    ui.end_row();
                }
            });
        });
        ui.add_space(10.0);
        ui.heading("Firmware and output level");
        toggle(ui, setter, &params.firmware_va, "The unit runs Baud Girl's FM-1+VA firmware");
        ui.label(
            "FM-1+VA takes LFO speed and delay as controllers (sent with every voice change) and has a master volume. The stock M-VAVE firmware ignores all three. The built-in synth behaves as the firmware chosen here.",
        );
        let shared_channel = params.key_channel.value() == params.fx_channel.value();
        ui.add_enabled_ui(params.firmware_va.value() && !shared_channel, |ui| {
            ui.horizontal(|ui| {
                ui.label("Volume");
                drag(ui, setter, &params.volume, 127);
            });
        });
        if shared_channel {
            ui.colored_label(BAD, "Volume needs different note and effect channels: on one channel CC 7 is the reverb mix.");
        }
        ui.add_space(6.0);
        ui.label(
            "Stock firmware has no MIDI volume. Distortion with gain 0 works as an output trim: its Level sets the volume.",
        );
        if ui.button("Use distortion as output trim").clicked() {
            set_bool(setter, &params.control_fx, true);
            if let [Fx::Switch(on), Fx::Value(gain), _, _] = &params.fx[12..16] {
                set_bool(setter, on, true);
                set_int(setter, gain, 0);
            }
        }
    }

    fn kit_tab(&mut self, ui: &mut egui::Ui, setter: &ParamSetter) {
        let params = self.params.clone();
        toggle(ui, setter, &params.kit_mode, "Kit mode: each key plays its own track's voice");
        ui.label("Voices switch per hit; ringing hits keep their sound. Keys without a track are silent.");
        ui.horizontal(|ui| {
            ui.label("Lookahead");
            drag(ui, setter, &params.kit_lead, 40);
            ui.weak("Hits play this much late so each voice change arrives first. The host is told, and compensates.");
        });
        ui.add_space(6.0);

        let before = params.shared.kit();
        let mut kit = before.clone();
        ui.horizontal(|ui| {
            match &self.loaded {
                Some(library) if !library.kits.is_empty() => {
                    self.kit_choice = self.kit_choice.min(library.kits.len() - 1);
                    egui::ComboBox::from_id_salt("kits")
                        .selected_text(library.kits[self.kit_choice].name.clone())
                        .show_ui(ui, |ui| {
                            for (i, choice) in library.kits.iter().enumerate() {
                                ui.selectable_value(&mut self.kit_choice, i, choice.name.clone());
                            }
                        });
                    if ui.button("Load kit").clicked() {
                        kit = library.kits[self.kit_choice].clone();
                        self.kit_track = None;
                        set_bool(setter, &params.kit_mode, true);
                    }
                }
                _ => {
                    ui.weak("No kits in the library");
                }
            }
            ui.separator();
            if ui.button("+ Track from current voice").clicked() {
                let voice = params.current_voice();
                let key = kit.free_key();
                kit.tracks.push(Track::new(&dx7::name_of(&voice), key, AUDITION_KEY, &voice));
                self.kit_track = Some(kit.tracks.len() - 1);
            }
            if ui.button("Clear kit").clicked() {
                kit = Kit::default();
                self.kit_track = None;
            }
        });
        ui.add_space(6.0);
        if kit.tracks.is_empty() {
            ui.weak("No tracks. Load a kit, or add a track from the current voice.");
        } else {
            ui.label(format!("{} · select a track, then click a library voice to replace its sound", kit.name));
        }

        let mut remove = None;
        egui::Grid::new("kit").num_columns(13).striped(true).spacing([6.0, 4.0]).show(ui, |ui| {
            if !kit.tracks.is_empty() {
                for heading in ["", "Key", "", "Track", "Voice", "Pitch", "Level", "Decay", "Release", "Tone", "Punch", "Grit", ""] {
                    ui.strong(heading);
                }
                ui.end_row();
            }
            for (i, track) in kit.tracks.iter_mut().enumerate() {
                if ui.selectable_label(self.kit_track == Some(i), format!(" {} ", i + 1)).clicked() {
                    self.kit_track = if self.kit_track == Some(i) { None } else { Some(i) };
                }
                ui.add(egui::DragValue::new(&mut track.key).range(0..=127).speed(0.1));
                ui.label(note_name(track.key));
                ui.add(egui::TextEdit::singleline(&mut track.name).desired_width(80.0));
                ui.monospace(track.voice_name());
                ui.add(egui::DragValue::new(&mut track.note).range(0..=127).speed(0.1))
                    .on_hover_text("Pitch played on the FM-1");
                let m = &mut track.macros;
                for (value, range) in [
                    (&mut m.level, -40..=20),
                    (&mut m.decay, -40..=40),
                    (&mut m.release, -40..=40),
                    (&mut m.tone, -40..=40),
                    (&mut m.punch, -40..=40),
                    (&mut m.grit, -7..=7),
                ] {
                    ui.add(egui::DragValue::new(value).range(range).speed(0.2));
                }
                ui.horizontal(|ui| {
                    if ui.small_button("Play").clicked() {
                        params.shared.play(track.key, AUDITION_VELOCITY, true);
                    }
                    if ui.small_button("Remove").clicked() {
                        remove = Some(i);
                    }
                });
                ui.end_row();
            }
        });
        if let Some(i) = remove {
            kit.tracks.remove(i);
            self.kit_track = None;
        }
        if kit != before {
            params.shared.set_kit(kit);
        }
    }
}

/// What is playing, for the top bar: (all is well, text).
fn sound_status(sound: Sound, connected: bool) -> (bool, &'static str) {
    match sound.sinks(connected, true) {
        (true, true) => (true, "FM-1 + built-in synth"),
        (true, false) if connected => (true, "FM-1 connected"),
        (false, true) if sound == Sound::BuiltIn => (true, "Built-in synth"),
        (false, true) => (true, "Built-in synth (FM-1 not found)"),
        _ => (false, "FM-1 not found"),
    }
}

/// A drop-down over (stored value, label) pairs. Returns true when the choice changed.
fn choice(ui: &mut egui::Ui, id: &str, value: &mut String, options: &[(String, String)]) {
    let shown = options.iter().find(|(v, _)| v == value).map_or(value.clone(), |(_, label)| label.clone());
    egui::ComboBox::from_id_salt(id).selected_text(shown).show_ui(ui, |ui| {
        for (option, label) in options {
            ui.selectable_value(value, option.clone(), label.clone());
        }
    });
}

fn owned(options: &[(&str, &str)]) -> Vec<(String, String)> {
    options.iter().map(|(v, l)| (v.to_string(), l.to_string())).collect()
}

impl Gui {
    fn speech_tab(&mut self, ui: &mut egui::Ui, setter: &ParamSetter) {
        let params = self.params.clone();
        let shared = params.shared.speech.clone();
        let status = shared.status();
        let mut settings = match self.speech.take() {
            Some(settings) => settings,
            None => {
                // First look at the tab: start the engine so the characters and status appear.
                let saved = shared.settings();
                if status.state == speech::State::Idle {
                    params.request_speech(saved.clone());
                }
                saved
            }
        };
        let before = settings.clone();

        ui.add(
            egui::TextEdit::multiline(&mut settings.text)
                .hint_text("Type what the FM-1 should say")
                .desired_rows(4)
                .desired_width(f32::INFINITY),
        );
        ui.horizontal(|ui| {
            // Some hosts keep the typing keyboard for themselves; the clipboard always works.
            if ui.button("Paste").on_hover_text("Replace the text with the clipboard's").clicked() {
                if let Some(text) = crate::platform::clipboard_text() {
                    settings.text = text;
                }
            }
            if ui.button("Clear").clicked() {
                settings.text.clear();
            }
            ui.weak("If typing does not reach the box in your host, copy the text and press Paste.");
        });
        ui.horizontal(|ui| {
            let speaking = shared.speaking.load(Ordering::Relaxed);
            let ready = status.keys_ready > 0;
            if speaking {
                if ui.button("Stop").clicked() {
                    shared.stop.store(true, Ordering::Relaxed);
                }
            } else if ui.add_enabled(ready, egui::Button::new("Speak")).clicked() {
                shared.speak.store(speech::SPEAK | settings.key as u32, Ordering::Release);
            }
            match &status.state {
                speech::State::Idle | speech::State::Loading if !ready => {
                    ui.weak("Preparing the speech engine...");
                }
                speech::State::Failed(error) => {
                    ui.colored_label(BAD, error.lines().next().unwrap_or("Speech is unavailable"));
                }
                _ => {
                    ui.label(&status.summary);
                    if ready && status.keys_ready < 25 {
                        ui.weak(format!("(preparing other pitches, {} of 25)", status.keys_ready));
                    }
                }
            }
        });
        ui.add_space(4.0);
        toggle(ui, setter, &params.speech_mode, "Speech mode: a note speaks the phrase at that note's pitch");
        ui.label(
            "While it speaks the FM-1 can play nothing else: each 20 ms is its own note and voice. Your voice or kit is sent again afterwards.",
        );
        ui.add_space(8.0);

        egui::Grid::new("speech").num_columns(4).spacing([14.0, 6.0]).show(ui, |ui| {
            let characters = if status.characters.is_empty() {
                vec![(settings.character.clone(), settings.character.clone())]
            } else {
                status.characters.clone()
            };
            ui.label("Character");
            choice(ui, "character", &mut settings.character, &characters);
            ui.label("Intonation");
            choice(ui, "tone", &mut settings.tone, &owned(&speech::TONES));
            ui.end_row();
            ui.label("Smoothness");
            choice(ui, "smooth", &mut settings.smooth, &owned(&speech::SMOOTHING));
            ui.label("Diphthongs");
            choice(ui, "diph", &mut settings.diph, &owned(&speech::DIPHTHONGS));
            ui.end_row();
            ui.label("Voice");
            choice(ui, "design", &mut settings.design, &owned(&speech::DESIGNS));
            ui.label("Pitch");
            ui.horizontal(|ui| {
                ui.add(egui::Slider::new(&mut settings.key, speech::KEY_MIN..=speech::KEY_MAX));
                ui.label(note_name(settings.key));
            });
            ui.end_row();
            ui.label("Speed");
            ui.add(egui::Slider::new(&mut settings.speed, 60..=160).suffix(" %"));
            ui.label("Accent");
            ui.add(egui::Slider::new(&mut settings.accent, 0..=8));
            ui.end_row();
            ui.label("Brightness");
            ui.add(egui::Slider::new(&mut settings.bright, 30..=75));
            ui.end_row();
        });

        if !status.lint.is_empty() {
            ui.add_space(8.0);
            ui.strong("How the text was read");
            for note in &status.lint {
                ui.label(note);
            }
        }
        if settings != before {
            params.request_speech(settings.clone());
        }
        self.speech = Some(settings);
    }
}

#[cfg(test)]
mod tests {
    //! The editor run without a window: egui lays the interface out and reports what it would
    //! draw, a recording host context captures what the controls ask the host to do, and clicks
    //! are aimed at the drawn text. Each frame can also be written as an SVG for inspection.

    use std::fmt::Write as _;
    use std::sync::Mutex as StdMutex;

    use egui::epaint::{ClippedShape, Shape};
    use egui::{vec2, Event, PointerButton, Pos2, RawInput, Rect};

    use super::*;
    use crate::params::EDITOR_SIZE;

    #[derive(Default)]
    struct Host {
        /// (parameter name, plain value text) for every value the editor set.
        sets: StdMutex<Vec<(String, String)>>,
        gestures: StdMutex<(u32, u32)>,
    }

    impl GuiContext for Host {
        fn plugin_api(&self) -> PluginApi {
            PluginApi::Standalone
        }

        fn request_resize(&self) -> bool {
            true
        }

        unsafe fn raw_begin_set_parameter(&self, _param: ParamPtr) {
            self.gestures.lock().unwrap().0 += 1;
        }

        unsafe fn raw_set_parameter_normalized(&self, param: ParamPtr, normalized: f32) {
            let (name, value) =
                unsafe { (param.name().to_string(), param.normalized_value_to_string(normalized, false)) };
            self.sets.lock().unwrap().push((name, value));
        }

        unsafe fn raw_end_set_parameter(&self, _param: ParamPtr) {
            self.gestures.lock().unwrap().1 += 1;
        }

        fn get_state(&self) -> nih_plug::wrapper::state::PluginState {
            unimplemented!("the editor never asks for the whole state")
        }

        fn set_state(&self, _state: nih_plug::wrapper::state::PluginState) {}
    }

    struct Rig {
        gui: Gui,
        ctx: egui::Context,
        host: Host,
        shapes: Vec<ClippedShape>,
    }

    impl Rig {
        fn new() -> Self {
            let params = Arc::new(Fm1Params::default());
            let gui = Gui {
                params,
                library: Arc::new(Mutex::new(State::Ready(Arc::new(sample_library())))),
                loaded: None,
                load_error: None,
                query: String::new(),
                filter: Filter::All,
                searched: None,
                results: Vec::new(),
                selected: None,
                tab: Tab::Voice,
                undo: Vec::new(),
                style: Style::Bell,
                mutate_amount: 15,
                rng: Rng::new(1),
                morph_a: None,
                morph_b: None,
                morph_t: 0.0,
                kit_choice: 0,
                kit_track: None,
                audition_on_load: true,
                name_edit: String::new(),
                speech: None,
            };
            let mut rig = Rig { gui, ctx: egui::Context::default(), host: Host::default(), shapes: Vec::new() };
            rig.frame(vec![]);
            rig.frame(vec![]); // egui sizes grids on the first pass and places them on the second
            rig
        }

        fn frame(&mut self, events: Vec<Event>) {
            let size = vec2(EDITOR_SIZE.0 as f32, EDITOR_SIZE.1 as f32);
            let input = RawInput {
                screen_rect: Some(Rect::from_min_size(Pos2::ZERO, size)),
                events,
                ..Default::default()
            };
            let setter = ParamSetter::new(&self.host);
            let output = self.ctx.run(input, |ctx| self.gui.show(ctx, &setter));
            self.shapes = output.shapes;
        }

        /// Every piece of text on screen with the rectangle it occupies.
        fn texts(&self) -> Vec<(String, Rect)> {
            fn walk(shape: &Shape, clip: Rect, out: &mut Vec<(String, Rect)>) {
                match shape {
                    Shape::Vec(shapes) => shapes.iter().for_each(|s| walk(s, clip, out)),
                    Shape::Text(text) => {
                        let rect = text.galley.rect.translate(text.pos.to_vec2());
                        if clip.intersects(rect) {
                            out.push((text.galley.text().to_string(), rect));
                        }
                    }
                    _ => {}
                }
            }
            let mut out = Vec::new();
            for clipped in &self.shapes {
                walk(&clipped.shape, clipped.clip_rect, &mut out);
            }
            out
        }

        fn find(&self, text: &str) -> Rect {
            let texts = self.texts();
            match texts.iter().find(|(t, _)| t.trim() == text) {
                Some((_, rect)) => *rect,
                None => panic!("no text {text:?} on screen; have {:?}", texts.iter().map(|t| &t.0).collect::<Vec<_>>()),
            }
        }

        fn shows(&self, text: &str) -> bool {
            self.texts().iter().any(|(t, _)| t.contains(text))
        }

        fn click(&mut self, text: &str) {
            let pos = self.find(text).center();
            let button = |pressed| Event::PointerButton {
                pos,
                button: PointerButton::Primary,
                pressed,
                modifiers: Default::default(),
            };
            self.frame(vec![Event::PointerMoved(pos)]);
            self.frame(vec![button(true)]);
            self.frame(vec![button(false)]);
            self.frame(vec![]);
        }

        fn take_sets(&self) -> Vec<(String, String)> {
            std::mem::take(&mut *self.host.sets.lock().unwrap())
        }

        /// Write the current frame as an SVG under target/, for looking at the layout.
        fn dump(&self, name: &str) {
            fn color(c: Color32) -> String {
                format!("rgba({},{},{},{:.2})", c.r(), c.g(), c.b(), c.a() as f32 / 255.0)
            }
            fn walk(shape: &Shape, clip: usize, out: &mut String) {
                match shape {
                    Shape::Vec(shapes) => shapes.iter().for_each(|s| walk(s, clip, out)),
                    Shape::Rect(r) => {
                        let _ = writeln!(
                            out,
                            r#"<rect clip-path="url(#c{clip})" x="{:.1}" y="{:.1}" width="{:.1}" height="{:.1}" fill="{}" stroke="{}" stroke-width="{:.1}"/>"#,
                            r.rect.min.x, r.rect.min.y, r.rect.width(), r.rect.height(),
                            color(r.fill), color(r.stroke.color), r.stroke.width
                        );
                    }
                    Shape::LineSegment { points, stroke } => {
                        let _ = writeln!(
                            out,
                            r#"<line clip-path="url(#c{clip})" x1="{:.1}" y1="{:.1}" x2="{:.1}" y2="{:.1}" stroke="{}" stroke-width="{:.1}"/>"#,
                            points[0].x, points[0].y, points[1].x, points[1].y, color(stroke.color), stroke.width
                        );
                    }
                    Shape::Text(text) => {
                        for row in &text.galley.rows {
                            let line: String = row.glyphs.iter().map(|g| g.chr).collect();
                            let Some(first) = row.glyphs.first() else { continue };
                            let section = &text.galley.job.sections[first.section_index as usize].format;
                            let fill = match (text.override_text_color, section.color) {
                                (Some(c), _) => c,
                                (None, c) if c == Color32::PLACEHOLDER => text.fallback_color,
                                (None, c) => c,
                            };
                            let family = match section.font_id.family {
                                egui::FontFamily::Monospace => "Menlo, monospace",
                                _ => "Helvetica, sans-serif",
                            };
                            let escaped = line.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;");
                            let _ = writeln!(
                                out,
                                r#"<text clip-path="url(#c{clip})" x="{:.1}" y="{:.1}" font-size="{:.1}" font-family="{family}" fill="{}" xml:space="preserve">{escaped}</text>"#,
                                text.pos.x + row.rect.min.x,
                                text.pos.y + row.rect.min.y + section.font_id.size * 0.85,
                                section.font_id.size,
                                color(fill)
                            );
                        }
                    }
                    _ => {}
                }
            }
            let (w, h) = EDITOR_SIZE;
            // A square canvas: Quick Look crops thumbnails of other shapes.
            let side = w.max(h);
            let mut svg = format!(
                r##"<svg xmlns="http://www.w3.org/2000/svg" width="{side}" height="{side}" viewBox="0 0 {side} {side}"><rect width="{w}" height="{h}" fill="#1b1b1b"/>"##
            );
            for (i, clipped) in self.shapes.iter().enumerate() {
                let c = clipped.clip_rect.intersect(Rect::from_min_size(Pos2::ZERO, vec2(w as f32, h as f32)));
                let _ = writeln!(
                    svg,
                    r#"<clipPath id="c{i}"><rect x="{:.1}" y="{:.1}" width="{:.1}" height="{:.1}"/></clipPath>"#,
                    c.min.x, c.min.y, c.width().max(0.0), c.height().max(0.0)
                );
                walk(&clipped.shape, i, &mut svg);
            }
            svg.push_str("</svg>\n");
            let dir = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("target").join("gui");
            let _ = std::fs::create_dir_all(&dir);
            let _ = std::fs::write(dir.join(format!("{name}.svg")), svg);
        }
    }

    /// Three voices and one two-track kit, all from a voice made for these tests.
    fn sample_library() -> Library {
        const VOICE: &str = "MWNjY2NjYwAnAAAAOAAAAgBjY2NjY2NjACcAAAA4DAACAGNjY2NjY2MAJwAAAEgAAAIAY2NjY2NjYwAnAAAAOABGBgBjY2NjY2NjACcAAAA4AFACAGNjY2NjY2MAJwAAADgAYwIAY2NjYzIyMjIVDygAAAAxGFRFU1QgTEVBRCA=";
        let index = format!(
            r#"{{"banks": ["My Banks/BANK1", "Other/Drums"], "voices": [
                ["TEST LEAD ", 0, 0, "{VOICE}", ["lead"]],
                ["KICK HARD ", 1, 5, "{VOICE}", ["kick"]],
                ["E.PIANO 1 ", 0, 10, "{VOICE}", ["keys"]]]}}"#
        );
        let kits = format!(
            r#"{{"kits": [{{"name": "Test kit", "tracks": [
                {{"name": "Kick", "note": 36, "voice": {{"b64": "{VOICE}"}}, "macros": {{"level": -16}}}},
                {{"name": "Hat", "note": 54, "voice": {{"b64": "{VOICE}"}}, "macros": {{}}}}]}}]}}"#
        );
        Library::parse(&index, Some(&kits)).unwrap()
    }

    #[test]
    fn note_names_follow_fl_studio_octaves() {
        let names = [60, 36, 61, 127].map(note_name);
        assert_eq!(names, ["C5", "C3", "C#5", "G10"]);
    }

    #[test]
    fn every_tab_lays_out_and_shows_its_controls() {
        let mut rig = Rig::new();
        rig.dump("voice");
        for text in ["Built-in synth (FM-1 not found)", "Auto", "Algorithm", "OP1", "OP6", "carrier", "Feedback", "Detune", "Transpose", "TEST LEAD    lead · BANK1 #1"] {
            assert!(rig.shows(text), "voice tab lacks {text:?}");
        }
        assert!(rig.shows("3 of 3"));
        for (tab, name, expected) in [
            ("Generate", "generate", vec!["Randomize", "Mutate", "Set A = current", "A to B"]),
            ("Effects", "effects", vec!["Filter", "Phaser", "Cutoff", "LP", "Room", "Use distortion as output trim", "FM-1+VA", "Volume"]),
            ("Drum kit", "kit", vec!["Kit mode: each key plays its own track's voice", "Load kit", "Test kit"]),
        ] {
            rig.click(tab);
            rig.dump(name);
            for text in expected {
                assert!(rig.shows(text), "{tab} tab lacks {text:?}");
            }
        }
        assert!(rig.take_sets().is_empty(), "looking at tabs must not change parameters");
    }

    #[test]
    fn the_sound_menu_sets_the_sound_parameter_and_the_status_follows_the_unit() {
        let mut rig = Rig::new();
        rig.click("Auto");
        rig.dump("sound-menu");
        rig.click("Both");
        assert_eq!(rig.take_sets(), vec![("Sound".to_string(), "Both".to_string())]);

        assert_eq!(sound_status(Sound::Auto, false), (true, "Built-in synth (FM-1 not found)"));
        assert_eq!(sound_status(Sound::Auto, true), (true, "FM-1 connected"));
        assert_eq!(sound_status(Sound::Fm1, false), (false, "FM-1 not found"));
        assert_eq!(sound_status(Sound::BuiltIn, true), (true, "Built-in synth"));
        assert_eq!(sound_status(Sound::Both, true), (true, "FM-1 + built-in synth"));
        assert_eq!(sound_status(Sound::Both, false), (true, "Built-in synth (FM-1 not found)"));
    }

    #[test]
    fn clicking_a_library_voice_sets_the_host_parameters_and_auditions() {
        let mut rig = Rig::new();
        rig.click("TEST LEAD    lead · BANK1 #1");
        let sets = rig.take_sets();
        // The test voice differs from the init voice in nine parameters; each is set once, as a
        // gesture, and nothing else is touched.
        assert_eq!(sets.len(), 9, "{sets:?}");
        assert!(sets.contains(&("Algorithm".to_string(), "22".to_string())));
        assert!(sets.contains(&("Feedback".to_string(), "7".to_string())));
        let gestures = *rig.host.gestures.lock().unwrap();
        assert_eq!((gestures.0 as usize, gestures.1 as usize), (sets.len(), sets.len()));
        assert_eq!(rig.gui.params.name.read().unwrap().as_str(), "TEST LEAD");
        assert_eq!(rig.gui.undo.len(), 1);
        let request = rig.gui.params.shared.audition.load(Ordering::Acquire);
        assert_eq!(request, crate::params::AUDITION | (AUDITION_KEY as u32) << 8 | 100);
    }

    #[test]
    fn speech_tab_compiles_what_is_typed_and_speak_asks_the_audio_thread() {
        let mut rig = Rig::new();
        rig.click("Speech");
        rig.dump("speech");
        for text in ["Character", "Smoothness", "Diphthongs", "Intonation", "Pitch", "Speed", "Brightness", "Speak", "Paste", "Clear"] {
            assert!(rig.shows(text), "speech tab lacks {text:?}");
        }
        assert!(rig.shows("Speech mode: a note speaks the phrase at that note's pitch"));
        if speech::app_dir().is_none() {
            return; // without the workbench the tab can only report that speech is unavailable
        }
        // Click into the text box, type a phrase, and let the engine compile it.
        let shared = rig.gui.params.shared.speech.clone();
        rig.click("Type what the FM-1 should say");
        rig.frame(vec![Event::Text("Hello there.".into())]);
        rig.frame(vec![]);
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(30);
        while shared.status().keys_ready < 25 {
            assert!(std::time::Instant::now() < deadline, "{:?}", shared.status());
            std::thread::sleep(std::time::Duration::from_millis(20));
        }
        rig.frame(vec![]);
        rig.frame(vec![]);
        rig.dump("speech-ready");
        assert!(rig.shows("parameter changes"), "no summary on screen");
        assert!(rig.shows("statement"), "no lint on screen");
        assert_eq!(shared.settings().text, "Hello there.");
        rig.click("Speak");
        assert_eq!(shared.speak.load(Ordering::Acquire), speech::SPEAK | 45);
        assert!(rig.take_sets().is_empty(), "speaking must not change host parameters");
    }

    #[test]
    fn randomize_sets_a_voice_in_the_chosen_style() {
        let mut rig = Rig::new();
        rig.click("Generate");
        rig.click("Randomize");
        let sets = rig.take_sets();
        let algorithm = sets.iter().find(|(name, _)| name == "Algorithm").map(|(_, v)| v.as_str());
        assert!(matches!(algorithm, Some("5" | "6" | "29" | "30" | "31")), "bell algorithm, got {algorithm:?}");
        assert!(rig.gui.params.name.read().unwrap().starts_with("RND BELL"));
        assert_eq!(rig.gui.undo.len(), 1);
    }

    #[test]
    fn loading_a_kit_turns_kit_mode_on_and_library_clicks_then_fill_the_selected_track() {
        let mut rig = Rig::new();
        rig.click("Drum kit");
        rig.click("Load kit");
        rig.dump("kit-loaded");
        assert_eq!(rig.take_sets(), vec![("Kit Mode".to_string(), "On".to_string())]);
        let kit = rig.gui.params.shared.kit();
        assert_eq!(kit.tracks.iter().map(|t| (t.name.as_str(), t.key)).collect::<Vec<_>>(), vec![("Kick", 36), ("Hat", 37)]);
        assert!(rig.shows("C3") && rig.shows("C#3"));
        assert!(rig.gui.params.shared.table.read().unwrap().slots[37].active);

        // Select track 2, then a library click replaces that track's voice, not the main voice.
        rig.click("2");
        assert!(rig.shows("Click loads into kit track: Hat"));
        let mut edited = kit.clone();
        edited.tracks[1].voice[0] = 1; // make the stored voice differ from the library's
        rig.gui.params.shared.set_kit(edited);
        rig.click("E.PIANO 1    keys · BANK1 #11");
        assert!(rig.take_sets().is_empty(), "the main voice must not change");
        assert_eq!(rig.gui.params.shared.kit().tracks[1].voice[0], 49);
        let request = rig.gui.params.shared.audition.load(Ordering::Acquire);
        assert_eq!(request & crate::params::AUDITION_KIT, crate::params::AUDITION_KIT);
        assert_eq!((request >> 8) & 0x7F, 37);
    }
}
