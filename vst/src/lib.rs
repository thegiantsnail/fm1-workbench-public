//! FM-1 Controller: an instrument plugin that plays and programs the M-VAVE FM-1 hardware synth.
//!
//! It produces no audio. Notes and parameter changes go straight to the FM-1's MIDI port, and
//! the voice is saved with the host project and sent again when the project loads.

use std::num::NonZeroU32;
use std::sync::atomic::Ordering;
use std::sync::Arc;

use nih_plug::prelude::*;

pub mod algo;
pub mod dx7;
pub mod editor;
pub mod engine;
pub mod kit;
pub mod library;
pub mod midi_out;
pub mod params;
pub mod speech;
pub mod status;
pub mod voicegen;

use engine::{kit_schedule, Engine, Msg};
use midi_out::{clock, MidiOut};
use params::{Fm1Params, AUDITION_KIT};

/// Every message is scheduled this far ahead so that none is already late when the sender
/// thread wakes; it shifts all notes equally.
const LEAD_SECONDS: f64 = 0.003;
const SUSTAIN_CC: u8 = 64;
/// Length of a note played from the editor's audition buttons.
const AUDITION_SECONDS: f32 = 0.35;
/// "Long ago" for kit note times, far enough from `i64::MIN` to subtract block lengths from.
const PAST: i64 = i64::MIN / 2;

pub struct Fm1 {
    params: Arc<Fm1Params>,
    engine: Engine,
    out: Option<MidiOut>,
    generation: u32,
    realtime: bool,
    sample_rate: f32,
    ticks_per_sample: f64,
    lead_ticks: u64,
    panic_armed: bool,
    resend_armed: bool,
    fx_controlled: bool,
    name: [u8; dx7::NAME_LEN],
    /// The audio thread's own copy of the kit, refreshed when the editor publishes a new one.
    kit: Box<kit::Table>,
    kit_generation: u32,
    /// Key of the audition note that is sounding, and the samples left before it is released.
    audition: Option<(u8, i64)>,
    /// When each key's kit note-on is due, in samples from the current block's start (negative
    /// once past), and the latest of them. See `engine::kit_schedule`.
    kit_note_at: [i64; 128],
    kit_last_note: i64,
    latency: u32,
    speech: speech::Runner,
    status: Option<status::Reporter>,
}

impl Default for Fm1 {
    fn default() -> Self {
        Fm1 {
            params: Arc::new(Fm1Params::default()),
            engine: Engine::default(),
            out: None,
            generation: 0,
            realtime: true,
            sample_rate: 44100.0,
            ticks_per_sample: 0.0,
            lead_ticks: 0,
            panic_armed: true,
            resend_armed: true,
            fx_controlled: false,
            name: *b"INIT VOICE",
            kit: Box::default(),
            kit_generation: 0,
            audition: None,
            kit_note_at: [PAST; 128],
            kit_last_note: PAST,
            latency: 0,
            speech: speech::Runner::default(),
            status: None,
        }
    }
}

impl Fm1 {
    fn channels(&self) -> (u8, u8) {
        ((self.params.key_channel.value() - 1) as u8, (self.params.fx_channel.value() - 1) as u8)
    }

    /// A trigger parameter fires once when switched on and re-arms when switched off.
    fn fired(armed: &mut bool, on: bool) -> bool {
        let fire = on && *armed;
        *armed = !on;
        fire
    }
}

/// Kit timing carried between notes and blocks.
struct KitClock<'a> {
    lead: i64,
    /// The unit runs FM-1+VA: a voice change also sends its LFO speed and delay.
    va: bool,
    note_at: &'a mut [i64; 128],
    last_note: &'a mut i64,
}

/// Start a note for `key`: through its kit track when there is one to use, else on the voice.
#[allow(clippy::too_many_arguments)]
fn start_note(
    engine: &mut Engine,
    kit: Option<&kit::Table>,
    clock: &mut KitClock,
    key: u8,
    velocity: u8,
    voice_shift: i32,
    channel: u8,
    offset: u32,
    send: &mut impl FnMut(Msg) -> bool,
) {
    let Some(kit) = kit else {
        return engine.note_on(key, velocity, voice_shift, channel, offset, send);
    };
    let key = key & 0x7F;
    let slot = &kit.slots[key as usize];
    if !slot.active {
        return; // a key without a track is silent in kit mode
    }
    // Sounding notes keep their voice, so the edit buffer can change once the last note is on.
    let (change, note) = kit_schedule(offset as i64, clock.lead, *clock.last_note);
    engine.sync_voice(&slot.voice, channel, change as u32, send);
    if clock.va {
        engine.sync_lfo(&slot.voice, channel, change as u32, send);
    }
    let pitch = slot.note as i32 + Engine::shift(&slot.voice);
    engine.note_on_as(key, pitch, velocity, channel, note as u32, send);
    clock.note_at[key as usize] = note;
    *clock.last_note = note;
}

impl Plugin for Fm1 {
    const NAME: &'static str = "FM-1 Controller";
    const VENDOR: &'static str = "FM-1 Workbench";
    const URL: &'static str = "";
    const EMAIL: &'static str = "";
    const VERSION: &'static str = env!("CARGO_PKG_VERSION");

    // Hosts expect an instrument to have an output; it stays silent. The FM-1's sound leaves
    // through the unit's own audio output.
    const AUDIO_IO_LAYOUTS: &'static [AudioIOLayout] = &[AudioIOLayout {
        main_input_channels: None,
        main_output_channels: NonZeroU32::new(2),
        ..AudioIOLayout::const_default()
    }];
    const MIDI_INPUT: MidiConfig = MidiConfig::MidiCCs;
    const SAMPLE_ACCURATE_AUTOMATION: bool = false;

    type SysExMessage = ();
    type BackgroundTask = ();

    fn params(&self) -> Arc<dyn Params> {
        self.params.clone()
    }

    fn editor(&mut self, _async_executor: AsyncExecutor<Self>) -> Option<Box<dyn Editor>> {
        editor::create(self.params.clone())
    }

    fn initialize(
        &mut self,
        _audio_io_layout: &AudioIOLayout,
        buffer_config: &BufferConfig,
        _context: &mut impl InitContext<Self>,
    ) -> bool {
        // The hardware cannot render faster than real time, so offline exports send nothing.
        self.realtime = buffer_config.process_mode != ProcessMode::Offline;
        self.sample_rate = buffer_config.sample_rate;
        self.ticks_per_sample = clock::rate() / buffer_config.sample_rate as f64;
        self.lead_ticks = (clock::rate() * LEAD_SECONDS) as u64;
        if self.out.is_none() {
            self.out = Some(MidiOut::start(self.params.shared.link.clone()));
        }
        self.speech.connect(&self.params.shared.speech);
        if self.status.is_none() {
            self.status = Some(status::Reporter::start(self.params.clone()));
            // FM1_SPEAK="some text" speaks it once at start-up, through the audio callback as
            // the Speak button would: a way to check speech in the standalone build.
            if let Ok(text) = std::env::var("FM1_SPEAK") {
                let settings = speech::Settings { text, ..speech::Settings::default() };
                let request = speech::SPEAK | settings.key as u32;
                self.params.request_speech(settings);
                self.params.shared.speech.speak.store(request, Ordering::Release);
            }
        }
        true
    }

    fn reset(&mut self) {
        // Called when playback stops or the host resets: nothing we started may keep sounding.
        let (key, _) = self.channels();
        if let Some(out) = &mut self.out {
            self.engine.release_all(key, &mut |msg| out.push(0, msg));
            if self.speech.stop(&mut |when, msg| out.push(when, msg)) {
                self.engine.forget(); // the phrase left its own voice in the unit
            }
        }
        self.params.shared.speech.speaking.store(false, Ordering::Relaxed);
        self.audition = None;
        self.kit_note_at = [PAST; 128];
        self.kit_last_note = PAST;
    }

    fn process(
        &mut self,
        buffer: &mut Buffer,
        _aux: &mut AuxiliaryBuffers,
        context: &mut impl ProcessContext<Self>,
    ) -> ProcessStatus {
        let samples = buffer.samples() as i64;
        for channel in buffer.as_slice() {
            channel.fill(0.0);
        }
        let Some(out) = &mut self.out else {
            return ProcessStatus::Normal;
        };
        if !self.realtime {
            while context.next_event().is_some() {}
            return ProcessStatus::Normal;
        }

        let shared = &self.params.shared;
        let (key, fx) = (
            (self.params.key_channel.value() - 1) as u8,
            (self.params.fx_channel.value() - 1) as u8,
        );
        let generation = out.link.generation.load(Ordering::Relaxed);
        let connected = out.link.connected.load(Ordering::Relaxed);
        let now = clock::now();
        let start = now + self.lead_ticks;
        let ticks_per_sample = self.ticks_per_sample;

        // Speech. A phrase is a ready-made list of timed messages; while one plays it owns the
        // unit, and afterwards the plugin's own voice is sent again.
        let ticks_per_ms = ticks_per_sample * self.sample_rate as f64 / 1000.0;
        let block_ms = samples as f64 / self.sample_rate as f64 * 1000.0;
        let horizon = start + ((block_ms + speech::HORIZON_MS) * ticks_per_ms) as u64;
        let spoke =
            self.speech.block(&shared.speech, now, horizon, ticks_per_ms, &mut |when, msg| out.push(when, msg));
        if spoke.started {
            self.engine.release_all(key, &mut |msg| out.push(0, msg));
        }
        if spoke.ended {
            self.engine.forget();
        }
        let speaking = self.speech.speaking();
        // Speech mode without a compiled phrase plays notes as usual: the plugin is never mute.
        let speech_mode =
            self.params.speech_mode.value() && shared.speech.available.load(Ordering::Acquire);
        shared.blocks.fetch_add(1, Ordering::Relaxed);
        let mut send = |msg: Msg| out.push(start + (msg.offset as f64 * ticks_per_sample) as u64, msg);

        // Take the editor's latest kit. try_read: never wait for the editor; next block will do.
        let kit_generation = shared.kit_generation.load(Ordering::Acquire);
        if kit_generation != self.kit_generation {
            if let Ok(table) = shared.table.try_read() {
                self.kit.slots = table.slots;
                self.kit_generation = kit_generation;
            }
        }
        let kit_mode = self.params.kit_mode.value();

        let resend = Self::fired(&mut self.resend_armed, self.params.resend.value())
            | shared.resend.swap(false, Ordering::Relaxed);
        if generation != self.generation || resend {
            self.generation = generation;
            self.engine.forget();
        }
        let panic = Self::fired(&mut self.panic_armed, self.params.panic.value())
            | shared.panic.swap(false, Ordering::Relaxed);
        if panic {
            self.engine.panic(key, &mut send);
            self.audition = None;
            if self.speech.stop(&mut |_, msg| send(msg)) {
                self.engine.forget();
            }
        }
        // `speaking` was read before a panic could end the phrase; the next block catches up.

        // Parameters go out ahead of this block's notes: a change applies to the next note. In
        // kit mode the voice parameters are not sent; each hit sends its own track's voice.
        let va = self.params.firmware_va.value();
        if connected && !speaking {
            if !kit_mode {
                if let Some(name) = self.params.name_bytes() {
                    self.name = name;
                }
                let want = self.params.edit_buffer(&self.name);
                self.engine.sync_voice(&want, key, 0, &mut send);
                if va {
                    self.engine.sync_lfo(&want, key, 0, &mut send);
                }
            }
            // On one shared channel CC 7 would be the reverb mix, so volume needs two channels.
            if va && key != fx {
                self.engine.sync_volume(self.params.volume.value() as u8, key, &mut send);
            }
            let control_fx = self.params.control_fx.value();
            if control_fx && !self.fx_controlled {
                self.engine.forget_fx(); // just switched on: the unit's settings are unknown
            }
            self.fx_controlled = control_fx;
            if control_fx {
                self.engine.sync_fx(&self.params.fx_values(), fx, &mut send);
            }
        }

        let shift = self.params.voice[dx7::TRANSPOSE].value() - dx7::TRANSPOSE_NEUTRAL as i32;
        let kit = kit_mode.then_some(&*self.kit);
        let lead = (self.params.kit_lead.value() as f32 * self.sample_rate / 1000.0) as i64;
        // Kit hits sound `lead` late; tell the host so it can line them up with other tracks.
        let latency = if kit_mode { lead as u32 } else { 0 };
        if latency != self.latency {
            self.latency = latency;
            context.set_latency_samples(latency);
        }
        let mut kit_clock = KitClock {
            lead,
            va,
            note_at: &mut self.kit_note_at,
            last_note: &mut self.kit_last_note,
        };

        // A note asked for by the editor's audition buttons, released after a fixed time.
        if let Some((note, left)) = self.audition {
            self.audition = Some((note, left - samples));
            if left - samples <= 0 {
                self.engine.note_off(note, key, 0, &mut send);
                self.audition = None;
                kit_clock.note_at[(note & 0x7F) as usize] = PAST;
            }
        }
        let request = shared.audition.swap(0, Ordering::AcqRel);
        if request != 0 && !speaking {
            let (note, velocity) = ((request >> 8) as u8 & 0x7F, request as u8 & 0x7F);
            let through = if request & AUDITION_KIT != 0 { Some(&*self.kit) } else { kit };
            start_note(&mut self.engine, through, &mut kit_clock, note, velocity, shift, key, 0, &mut send);
            self.audition = Some((note, (AUDITION_SECONDS * self.sample_rate) as i64));
        }

        while let Some(event) = context.next_event() {
            if matches!(event, NoteEvent::NoteOn { .. }) {
                shared.notes.fetch_add(1, Ordering::Relaxed);
            }
            match event {
                NoteEvent::NoteOn { timing, note, .. } if speech_mode => {
                    // The note starts the phrase at its own pitch; it begins on the next block,
                    // timed from here.
                    let at = start + (timing as f64 * ticks_per_sample) as u64;
                    self.speech.trigger(note, at, ticks_per_ms);
                }
                NoteEvent::NoteOn { .. } if speaking => {} // the unit is busy speaking
                NoteEvent::NoteOn { timing, note, velocity, .. } => {
                    let velocity = (velocity * 127.0).round() as u8;
                    start_note(
                        &mut self.engine,
                        kit,
                        &mut kit_clock,
                        note,
                        velocity,
                        shift,
                        key,
                        timing,
                        &mut send,
                    );
                }
                NoteEvent::NoteOff { timing, note, .. } | NoteEvent::Choke { timing, note, .. } => {
                    // A kit note was delayed by the lookahead: its note-off keeps the same
                    // length and can never come before the note-on.
                    let due = kit_clock.note_at[(note & 0x7F) as usize];
                    let at = if due > PAST { (timing as i64 + lead).max(due + 1) } else { timing as i64 };
                    self.engine.note_off(note, key, at as u32, &mut send);
                    kit_clock.note_at[(note & 0x7F) as usize] = PAST;
                }
                NoteEvent::MidiCC { timing, cc: SUSTAIN_CC, value, .. } => {
                    self.engine.sustain(value >= 0.5, key, timing, &mut send);
                }
                _ => {}
            }
        }
        // Kit note times are relative to this block's start: move them to the next block's.
        for at in self.kit_note_at.iter_mut().filter(|at| **at > PAST) {
            *at -= samples;
        }
        if self.kit_last_note > PAST {
            self.kit_last_note -= samples;
        }
        // Not `Normal`: a host that suspends silent plugins would stop calling us, and parameter
        // changes made while no note plays would wait for the next note.
        ProcessStatus::KeepAlive
    }

    fn deactivate(&mut self) {
        self.reset();
    }
}

impl Drop for Fm1 {
    fn drop(&mut self) {
        // The host may destroy the plugin without deactivating it. Queue the releases; dropping
        // `out` afterwards sends what is queued before its thread stops.
        self.reset();
    }
}

impl ClapPlugin for Fm1 {
    const CLAP_ID: &'static str = "com.fm1-workbench.fm1-controller";
    const CLAP_DESCRIPTION: Option<&'static str> =
        Some("Plays and programs the M-VAVE FM-1 hardware synth");
    const CLAP_MANUAL_URL: Option<&'static str> = None;
    const CLAP_SUPPORT_URL: Option<&'static str> = None;
    const CLAP_FEATURES: &'static [ClapFeature] =
        &[ClapFeature::Instrument, ClapFeature::Synthesizer, ClapFeature::Utility];
}

impl Vst3Plugin for Fm1 {
    const VST3_CLASS_ID: [u8; 16] = *b"FM1CtrlMVaveWkbn";
    const VST3_SUBCATEGORIES: &'static [Vst3SubCategory] =
        &[Vst3SubCategory::Instrument, Vst3SubCategory::Synth];
}

nih_export_clap!(Fm1);
nih_export_vst3!(Fm1);
