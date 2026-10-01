//! FM-1 Controller: an instrument plugin that plays and programs the M-VAVE FM-1 hardware synth.
//!
//! Notes and parameter changes go straight to the FM-1's MIDI port, and the voice is saved with
//! the host project and sent again when the project loads. The FM-1's sound leaves through the
//! unit's own audio output; when the unit is not there, or for an offline render, the same
//! messages play the built-in software FM-1 (`synth`) and the plugin makes the sound itself.

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
pub mod platform;
pub mod speech;
pub mod status;
pub mod synth;
pub mod voicegen;

use engine::{kit_schedule, Engine, Msg};
use midi_out::{clock, MidiOut};
use params::{Fm1Params, AUDITION_KIT};
use synth::Synth;

/// Every message is scheduled this far ahead so that none is already late when the sender
/// thread wakes; it shifts all notes equally.
const LEAD_SECONDS: f64 = 0.003;
const SUSTAIN_CC: u8 = 64;
/// Length of a note played from the editor's audition buttons.
const AUDITION_SECONDS: f32 = 0.35;
/// "Long ago" for kit note times, far enough from `i64::MIN` to subtract block lengths from.
const PAST: i64 = i64::MIN / 2;
/// Where the sample-counting clock starts. Not 0: a message time of 0 means "now".
const VIRTUAL_EPOCH: u64 = 1 << 32;

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
    /// The built-in software FM-1, and what is playing now: (the FM-1, the built-in synth).
    synth: Synth,
    sinks: (bool, bool),
    /// Samples processed so far: the clock when the FM-1 is not playing.
    frames: u64,
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
            synth: Synth::new(44100.0),
            sinks: (false, false),
            frames: 0,
        }
    }
}

/// Where a block's messages go: the FM-1, the built-in synth, or both. The two are fed the same
/// messages at the same times, the FM-1 in host time and the synth in samples.
struct Sinks<'a> {
    out: &'a mut MidiOut,
    synth: &'a mut Synth,
    hardware: bool,
    software: bool,
    /// Host time of the block's first sample.
    start: u64,
    ticks_per_sample: f64,
    /// The synth's frame at the block's first sample.
    frame: u64,
}

impl Sinks<'_> {
    /// Send `msg` at host time `when`; 0 means now, ahead of anything timed. Returns false,
    /// sending nothing, when a queue is full.
    fn at(&mut self, when: u64, msg: Msg) -> bool {
        // All or nothing: a message that one took and the other refused would be sent twice.
        if (self.hardware && self.out.full()) || (self.software && self.synth.full()) {
            return false;
        }
        if self.hardware {
            self.out.push(when, msg);
        }
        if self.software {
            if when == 0 {
                self.synth.queue_now(&msg);
            } else {
                let ahead = when.saturating_sub(self.start) as f64 / self.ticks_per_sample;
                self.synth.queue(self.frame + ahead.round() as u64, &msg);
            }
        }
        true
    }

    /// Send `msg` at its own offset, in samples from the block's first.
    fn timed(&mut self, msg: Msg) -> bool {
        self.at(self.start + (msg.offset as f64 * self.ticks_per_sample) as u64, msg)
    }
}

impl Fm1 {
    fn channels(&self) -> (u8, u8) {
        ((self.params.key_channel.value() - 1) as u8, (self.params.fx_channel.value() - 1) as u8)
    }

    fn configure(&mut self, sample_rate: f32, realtime: bool) {
        self.realtime = realtime;
        self.sample_rate = sample_rate;
        if self.synth.sample_rate() != sample_rate {
            self.synth = Synth::new(sample_rate);
            self.engine.forget(); // the new synth holds the init voice, not ours
        }
        self.ticks_per_sample = clock::rate() / sample_rate as f64;
        self.lead_ticks = (clock::rate() * LEAD_SECONDS) as u64;
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

    // The built-in synth's sound, mono on both channels. Silent while only the FM-1 plays: its
    // sound leaves through the unit's own audio output.
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
        // The hardware cannot render faster than real time, so offline exports send it nothing
        // and play the built-in synth instead.
        self.configure(buffer_config.sample_rate, buffer_config.process_mode != ProcessMode::Offline);
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
            let (hardware, software) = self.sinks;
            let frame = self.synth.now();
            let mut sinks = Sinks {
                out,
                synth: &mut self.synth,
                hardware,
                software,
                start: 0,
                ticks_per_sample: self.ticks_per_sample.max(1.0),
                frame,
            };
            self.engine.release_all(key, &mut |msg| sinks.at(0, msg));
            if self.speech.stop(&mut |when, msg| sinks.at(when, msg)) {
                self.engine.forget(); // the phrase left its own voice in the unit
            }
        }
        // The synth takes in what is still queued for it, then falls silent at once.
        self.synth.silence();
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
        let Some(out) = &mut self.out else {
            for channel in buffer.as_slice() {
                channel.fill(0.0);
            }
            return ProcessStatus::Normal;
        };

        let shared = &self.params.shared;
        let (key, fx) = (
            (self.params.key_channel.value() - 1) as u8,
            (self.params.fx_channel.value() - 1) as u8,
        );
        let generation = out.link.generation.load(Ordering::Relaxed);
        let connected = out.link.connected.load(Ordering::Relaxed);
        let ticks_per_sample = self.ticks_per_sample;
        let va = self.params.firmware_va.value();

        // What plays this block. When that changes, what is sounding is released where it was
        // started, and everything is sent again: the newcomer holds some other voice.
        let (hardware, software) = self.params.sound_mode().sinks(connected, self.realtime);
        if (hardware, software) != self.sinks {
            let frame = self.synth.now();
            let mut old = Sinks {
                out: &mut *out,
                synth: &mut self.synth,
                hardware: self.sinks.0,
                software: self.sinks.1,
                start: 0,
                ticks_per_sample,
                frame,
            };
            self.engine.release_all(key, &mut |msg| old.at(0, msg));
            self.speech.stop(&mut |_, msg| old.at(0, msg));
            self.engine.forget();
            self.audition = None;
            self.kit_note_at = [PAST; 128];
            self.kit_last_note = PAST;
            self.sinks = (hardware, software);
            shared.hardware.store(hardware, Ordering::Relaxed);
            shared.software.store(software, Ordering::Relaxed);
        }
        if software {
            self.synth.set_firmware_va(va);
            self.synth.set_fx_channel(fx);
        }

        // The FM-1 is timed by the host's clock. Without it time is counted in samples, which
        // also holds when a render runs faster or slower than real time.
        let now = if hardware {
            clock::now()
        } else {
            VIRTUAL_EPOCH + (self.frames as f64 * ticks_per_sample) as u64
        };
        let start = now + self.lead_ticks;
        self.frames += samples as u64;
        let frame = self.synth.now();
        let mut sinks = Sinks {
            out,
            synth: &mut self.synth,
            hardware,
            software,
            start,
            ticks_per_sample,
            frame,
        };

        // Speech. A phrase is a ready-made list of timed messages; while one plays it owns the
        // unit, and afterwards the plugin's own voice is sent again.
        let ticks_per_ms = ticks_per_sample * self.sample_rate as f64 / 1000.0;
        let block_ms = samples as f64 / self.sample_rate as f64 * 1000.0;
        let horizon = start + ((block_ms + speech::HORIZON_MS) * ticks_per_ms) as u64;
        let spoke =
            self.speech.block(&shared.speech, now, horizon, ticks_per_ms, &mut |when, msg| sinks.at(when, msg));
        if spoke.started {
            self.engine.release_all(key, &mut |msg| sinks.at(0, msg));
        }
        if spoke.ended {
            self.engine.forget();
        }
        let speaking = self.speech.speaking();
        // Speech mode without a compiled phrase plays notes as usual: the plugin is never mute.
        let speech_mode =
            self.params.speech_mode.value() && shared.speech.available.load(Ordering::Acquire);
        shared.blocks.fetch_add(1, Ordering::Relaxed);
        let mut send = |msg: Msg| sinks.timed(msg);

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
        if (software || connected) && !speaking {
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
        // Only the hardware needs time to take in a voice change; the synth does it at once.
        let lead = if hardware {
            (self.params.kit_lead.value() as f32 * self.sample_rate / 1000.0) as i64
        } else {
            0
        };
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

        // The built-in synth plays what it was sent, and rings out after it stops being fed.
        let output = buffer.as_slice();
        if software || self.synth.active() {
            if let Some((first, others)) = output.split_first_mut() {
                self.synth.render(first);
                for channel in others {
                    channel.copy_from_slice(first);
                }
            }
        } else {
            for channel in output {
                channel.fill(0.0);
            }
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
        Some("Plays and programs the M-VAVE FM-1 hardware synth, with a software FM-1 built in");
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

#[cfg(test)]
mod tests {
    //! The plugin's audio callback, driven as a host drives it, with the MIDI output replaced by
    //! one that records what would have gone to the FM-1.

    use std::cell::Cell;
    use std::collections::VecDeque;

    use super::*;
    use crate::params::Sound;

    const SR: f32 = 44100.0;
    const BLOCK: usize = 256;

    struct Host {
        events: VecDeque<PluginNoteEvent<Fm1>>,
        latency: Cell<u32>,
    }

    impl ProcessContext<Fm1> for Host {
        fn plugin_api(&self) -> PluginApi {
            PluginApi::Standalone
        }
        fn execute_background(&self, _task: ()) {}
        fn execute_gui(&self, _task: ()) {}
        fn transport(&self) -> &Transport {
            unimplemented!("the plugin does not read the transport")
        }
        fn next_event(&mut self) -> Option<PluginNoteEvent<Fm1>> {
            self.events.pop_front()
        }
        fn send_event(&mut self, _event: PluginNoteEvent<Fm1>) {}
        fn set_latency_samples(&self, samples: u32) {
            self.latency.set(samples);
        }
        fn set_current_voice_capacity(&self, _capacity: u32) {}
    }

    struct Rig {
        plugin: Fm1,
        sent: midi_out::Sent,
        host: Host,
    }

    fn on(note: u8, timing: u32) -> PluginNoteEvent<Fm1> {
        NoteEvent::NoteOn { timing, voice_id: None, channel: 0, note, velocity: 100.0 / 127.0 }
    }

    fn off(note: u8, timing: u32) -> PluginNoteEvent<Fm1> {
        NoteEvent::NoteOff { timing, voice_id: None, channel: 0, note, velocity: 0.0 }
    }

    fn rms(samples: &[f32]) -> f32 {
        (samples.iter().map(|s| s * s).sum::<f32>() / samples.len().max(1) as f32).sqrt()
    }

    /// Zero crossings per second, halved: the pitch of a clean tone.
    fn pitch(samples: &[f32]) -> f32 {
        let crossings = samples.windows(2).filter(|w| (w[0] < 0.0) != (w[1] < 0.0)).count();
        crossings as f32 / 2.0 / (samples.len() as f32 / SR)
    }

    impl Rig {
        fn new(sound: Sound, realtime: bool, connected: bool) -> Rig {
            let mut plugin = Fm1::default();
            let link = plugin.params.shared.link.clone();
            link.connected.store(connected, Ordering::Relaxed);
            let (out, sent) = MidiOut::detached(link);
            plugin.out = Some(out);
            plugin.configure(SR, realtime);
            let mut rig = Rig { plugin, sent, host: Host { events: VecDeque::new(), latency: Cell::new(0) } };
            rig.set(|setup| setup.sound = sound);
            rig
        }

        /// Change parameters as a host would between blocks. Only a host can set a parameter in
        /// place, so the plugin is given a new set that starts from the changed values.
        fn set(&mut self, change: impl FnOnce(&mut params::Setup)) {
            let old = &self.plugin.params;
            let mut setup = params::Setup {
                voice: old.current_voice(),
                sound: old.sound_mode(),
                kit_mode: old.kit_mode.value(),
                shared: old.shared.clone(),
            };
            change(&mut setup);
            self.plugin.params = Arc::new(Fm1Params::new(setup));
        }

        /// One audio block. Returns the left channel; the right must be the same.
        fn block(&mut self, events: Vec<PluginNoteEvent<Fm1>>) -> Vec<f32> {
            self.host.events.extend(events);
            let (mut left, mut right) = (vec![9f32; BLOCK], vec![9f32; BLOCK]);
            let mut buffer = Buffer::default();
            // SAFETY: both slices outlive `buffer` and are BLOCK samples long.
            unsafe {
                buffer.set_slices(BLOCK, |slices| {
                    let left: &mut [f32] = &mut *(left.as_mut_slice() as *mut [f32]);
                    let right: &mut [f32] = &mut *(right.as_mut_slice() as *mut [f32]);
                    *slices = vec![left, right];
                });
            }
            let mut aux = AuxiliaryBuffers { inputs: &mut [], outputs: &mut [] };
            let status = self.plugin.process(&mut buffer, &mut aux, &mut self.host);
            assert!(matches!(status, ProcessStatus::KeepAlive));
            drop(buffer);
            assert_eq!(left, right);
            assert!(left.iter().all(|s| s.is_finite() && s.abs() <= 1.0));
            left
        }

        fn run(&mut self, seconds: f32) -> Vec<f32> {
            let blocks = (seconds * SR / BLOCK as f32).ceil() as usize;
            (0..blocks).flat_map(|_| self.block(vec![])).collect()
        }
    }

    #[test]
    fn without_the_fm1_the_built_in_synth_plays_the_notes() {
        let mut rig = Rig::new(Sound::Auto, true, false);
        let first = rig.block(vec![on(69, 100)]);
        assert!(first[..100].iter().all(|&s| s == 0.0), "nothing sounds before the note's own sample");
        let held = rig.run(0.5);
        // The init voice is a sine: A4 at 440 Hz, one carrier at the synth's fixed gain.
        assert!((0.055..0.070).contains(&rms(&held[4410..])), "{}", rms(&held[4410..]));
        assert!((pitch(&held[4410..]) - 440.0).abs() < 3.0, "{}", pitch(&held[4410..]));
        rig.block(vec![off(69, 0)]);
        let after = rig.run(0.6);
        assert!(rms(&after[13230..]) < 1e-4);
        // Nothing went to the FM-1, and the plugin says what is playing.
        assert!(rig.sent.take().is_empty());
        let shared = &rig.plugin.params.shared;
        assert!(shared.software.load(Ordering::Relaxed) && !shared.hardware.load(Ordering::Relaxed));
    }

    #[test]
    fn with_the_fm1_connected_the_plugin_stays_silent_and_sends_to_the_unit() {
        let mut rig = Rig::new(Sound::Auto, true, true);
        let mut out = rig.block(vec![on(69, 100)]);
        out.extend(rig.run(0.2));
        assert!(out.iter().all(|&s| s == 0.0));
        let sent = rig.sent.take();
        assert_eq!(sent.len(), dx7::EDIT_SIZE + 1); // the whole voice, then the note
        assert_eq!(sent.last().unwrap().1.data(), &[0x90, 69, 100]);
        assert!(sent.iter().all(|(when, _)| *when > 0));
    }

    #[test]
    fn both_plays_the_unit_and_the_synth_from_the_same_messages() {
        let mut rig = Rig::new(Sound::Both, true, true);
        rig.block(vec![on(57, 0)]);
        let out = rig.run(0.3);
        assert!((pitch(&out[4410..]) - 220.0).abs() < 3.0);
        assert_eq!(rig.sent.take().len(), dx7::EDIT_SIZE + 1);
        // "FM-1 only" is silent even when the unit is missing; "Built-in" ignores a unit that is there.
        let mut only = Rig::new(Sound::Fm1, true, false);
        only.block(vec![on(57, 0)]);
        assert!(only.run(0.1).iter().all(|&s| s == 0.0));
        let mut built_in = Rig::new(Sound::BuiltIn, true, true);
        built_in.block(vec![on(57, 0)]);
        assert!(rms(&built_in.run(0.1)) > 0.03 && built_in.sent.take().is_empty());
    }

    #[test]
    fn an_offline_render_uses_the_synth_even_with_the_unit_connected() {
        let mut rig = Rig::new(Sound::Auto, false, true);
        rig.block(vec![on(69, 0)]);
        let out = rig.run(0.3);
        assert!((pitch(&out[4410..]) - 440.0).abs() < 3.0);
        assert!(rig.sent.take().is_empty());
        // Two offline renders of the same notes are identical: time is counted in samples.
        let render = || {
            let mut rig = Rig::new(Sound::Auto, false, false);
            let mut out = rig.block(vec![on(60, 7), on(64, 90)]);
            out.extend(rig.block(vec![off(60, 200), on(67, 201)]));
            out.extend(rig.run(0.2));
            out
        };
        assert_eq!(render(), render());
    }

    #[test]
    fn voice_parameters_and_transpose_reach_the_synth() {
        let mut rig = Rig::new(Sound::BuiltIn, true, false);
        rig.block(vec![on(69, 0)]);
        let loud = rms(&rig.run(0.3)[4410..]);
        rig.block(vec![off(69, 0)]);
        rig.run(0.3);
        // Operator 1 output level 99 -> 79: 20 steps of 0.75 dB. Transpose an octave up.
        rig.set(|setup| {
            setup.voice[dx7::op_index(1, dx7::op::OL)] = 79;
            setup.voice[dx7::TRANSPOSE] = 36;
        });
        rig.block(vec![on(57, 0)]);
        let out = rig.run(0.3);
        let db = 20.0 * (rms(&out[4410..]) / loud).log10();
        assert!((-15.6..-14.4).contains(&db), "{db}");
        assert!((pitch(&out[4410..]) - 440.0).abs() < 3.0, "{}", pitch(&out[4410..]));
        assert!(rig.sent.take().is_empty());
    }

    #[test]
    fn when_the_fm1_appears_the_synth_lets_go_and_the_unit_takes_over() {
        let mut rig = Rig::new(Sound::Auto, true, false);
        rig.block(vec![on(69, 0)]);
        assert!(rms(&rig.run(0.2)) > 0.03);
        // The sender thread found the unit.
        let link = rig.plugin.params.shared.link.clone();
        link.connected.store(true, Ordering::Relaxed);
        link.generation.fetch_add(1, Ordering::Relaxed);
        let out = rig.run(0.6);
        assert!(rms(&out[..2048]) > 1e-3, "the held note rings out rather than cutting off");
        assert!(rms(&out[13230..]) < 1e-4);
        assert_eq!(rig.sent.take().len(), dx7::EDIT_SIZE); // the whole voice, for the next note
        rig.run(0.6);
        rig.block(vec![on(60, 0)]);
        assert!(rig.run(0.1).iter().all(|&s| s == 0.0));
        assert_eq!(rig.sent.take().last().unwrap().1.data(), &[0x90, 60, 100]);
        // And back: the unit goes to sleep, the next note is the synth's.
        link.connected.store(false, Ordering::Relaxed);
        rig.block(vec![]);
        assert!(rig.sent.take().iter().any(|(_, msg)| msg.data() == [0x80, 60, 0]), "the unit's note is released");
        rig.block(vec![on(60, 0)]);
        assert!(rms(&rig.run(0.1)) > 0.03);
    }

    #[test]
    fn reset_and_panic_silence_the_synth() {
        let mut rig = Rig::new(Sound::BuiltIn, true, false);
        rig.block(vec![on(60, 0), on(64, 0), on(67, 0)]);
        assert!(rms(&rig.run(0.1)) > 0.03);
        rig.plugin.reset();
        assert!(rig.run(0.05).iter().all(|&s| s == 0.0));

        rig.block(vec![on(60, 0)]);
        assert!(rms(&rig.run(0.1)) > 0.03);
        rig.plugin.params.shared.panic.store(true, Ordering::Relaxed);
        let out = rig.run(0.6);
        assert!(rms(&out[13230..]) < 1e-4);
    }

    #[test]
    fn a_kit_plays_each_key_with_its_own_voice_and_no_lookahead_on_the_synth() {
        use crate::kit::{Kit, Track};

        let mut rig = Rig::new(Sound::BuiltIn, true, false);
        let mut quiet = dx7::init_voice();
        quiet[dx7::op_index(1, dx7::op::OL)] = 79;
        let kit = Kit {
            name: "Test".into(),
            tracks: vec![Track::new("Low", 36, 57, &dx7::init_voice()), Track::new("High", 38, 81, &quiet)],
        };
        rig.plugin.params.shared.set_kit(kit);
        rig.set(|setup| setup.kit_mode = true);
        // Both hits in one block, 64 samples apart: each sounds with its own voice.
        let out: Vec<f32> = rig.block(vec![on(36, 0), on(38, 64)]).into_iter().chain(rig.run(0.3)).collect();
        assert_eq!(rig.host.latency.get(), 0);
        assert!(out[1..40].iter().any(|&s| s.abs() > 1e-4), "the first hit is not delayed");
        let tone = |hz: f32| {
            let (re, im) = out[4410..].iter().enumerate().fold((0f32, 0f32), |(re, im), (i, s)| {
                let angle = 2.0 * std::f32::consts::PI * hz * i as f32 / SR;
                (re + s * angle.cos(), im + s * angle.sin())
            });
            2.0 * (re * re + im * im).sqrt() / (out.len() - 4410) as f32
        };
        let db = 20.0 * (tone(880.0) / tone(220.0)).log10();
        assert!((-17.0..-13.5).contains(&db), "{db}");
        // A key without a track is silent in kit mode.
        rig.block(vec![off(36, 0), off(38, 0)]);
        rig.run(0.6);
        rig.block(vec![on(50, 0)]);
        assert!(rms(&rig.run(0.1)) < 1e-4);

        // With the unit, kit hits wait for the voice change and the host is told the latency.
        let mut unit = Rig::new(Sound::Auto, true, true);
        unit.set(|setup| setup.kit_mode = true);
        unit.block(vec![]);
        assert_eq!(unit.host.latency.get(), 882); // 20 ms
    }

    #[test]
    fn speech_plays_through_the_synth_on_the_sample_clock() {
        if speech::app_dir().is_none() {
            eprintln!("workbench speech engine not present; skipped");
            return;
        }
        let mut rig = Rig::new(Sound::Auto, false, false); // offline: only the sample clock moves
        let shared = rig.plugin.params.shared.speech.clone();
        rig.plugin.speech.connect(&shared);
        let settings = speech::Settings { text: "Hello there.".into(), ..speech::Settings::default() };
        let key = settings.key;
        rig.plugin.params.request_speech(settings);
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(60);
        while !shared.available.load(Ordering::Acquire) || shared.phrase(key).is_none() {
            assert!(std::time::Instant::now() < deadline, "{:?}", shared.status());
            std::thread::sleep(std::time::Duration::from_millis(20));
        }
        let length = shared.phrase(key).unwrap().duration_ms() as f32 / 1000.0;
        shared.speak.store(speech::SPEAK | key as u32, Ordering::Release);
        let spoken = rig.run(length + 0.2);
        assert!(shared.speaking.load(Ordering::Relaxed) || rms(&spoken) > 0.0);
        assert!(rms(&spoken) > 0.005, "{}", rms(&spoken));
        // Loud and quiet stretches: syllables, not one held tone.
        let loudness: Vec<f32> = spoken.chunks(1024).map(rms).collect();
        let peak = loudness.iter().cloned().fold(0f32, f32::max);
        assert!(loudness.iter().filter(|&&l| l < 0.25 * peak).count() >= 3, "{loudness:?}");
        // It ends by itself, and the plugin's own voice comes back for the next note.
        let after = rig.run(2.5);
        assert!(!shared.speaking.load(Ordering::Relaxed));
        assert!(rms(&after[after.len() - 4096..]) < 1e-4);
        rig.block(vec![on(69, 0)]);
        let note = rig.run(0.3);
        assert!((pitch(&note[4410..]) - 440.0).abs() < 3.0, "{}", pitch(&note[4410..]));
        assert!(rig.sent.take().is_empty());
    }
}
