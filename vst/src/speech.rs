//! Speech: typed text spoken by the FM-1 as one DX7 voice per 20 ms frame.
//!
//! The engine is the workbench's own `app/speech.js`, run unmodified in an embedded JavaScript
//! engine (QuickJS) on its own thread, so the plugin speaks exactly as the workbench does and
//! follows it as that code changes. `speech_glue.js` mirrors what the workbench's Speech tab does
//! to build a phrase. The result is a list of timed MIDI messages; the audio thread plays it.
//!
//! A phrase is compiled once per pitch (keys 36 to 60, the workbench's Pitch range), the chosen
//! key first, so a MIDI note can start it at that note's pitch with no delay.

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::mpsc::{self, Receiver, Sender};
use std::sync::{Arc, Mutex};

use serde::{Deserialize, Serialize};

use crate::engine::Msg;
use crate::library;

pub const KEY_MIN: u8 = 36;
pub const KEY_MAX: u8 = 60;
const KEYS: usize = (KEY_MAX - KEY_MIN + 1) as usize;
/// The audio thread asks for events this far beyond the block it is processing.
pub const HORIZON_MS: f64 = 6.0;
/// A finished phrase is considered over this long after its last message.
const TAIL_MS: f64 = 100.0;

pub const SMOOTHING: [(&str, &str); 4] = [
    ("soft", "Soft joins (crossfade vowels)"),
    ("sync", "Sync (phase-aligned)"),
    ("smooth", "Smooth (crossfade + glide)"),
    ("off", "Off"),
];
pub const DIPHTHONGS: [(&str, &str); 3] =
    [("glide", "One-note glide"), ("all", "All vowels as one note"), ("off", "Note chain")];
pub const DESIGNS: [(&str, &str); 4] = [
    ("fixedfm", "Exact formants"),
    ("harmonic", "Harmonic (Chowning)"),
    ("lebrun", "Harmonic pairs (Le Brun)"),
    ("sine", "Sine-wave speech"),
];
pub const TONES: [(&str, &str); 2] = [("grammar", "Grammar"), ("flat", "Flat (robot)")];

/// What the Speech tab holds; saved with the project. Field values are the workbench's own.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct Settings {
    pub text: String,
    pub character: String,
    pub tone: String,
    pub key: u8,
    /// Percent, 60 to 160.
    pub speed: u32,
    pub accent: u32,
    pub bright: u32,
    pub design: String,
    pub smooth: String,
    pub diph: String,
}

impl Default for Settings {
    fn default() -> Self {
        Settings {
            text: String::new(),
            character: "natural".into(),
            tone: "grammar".into(),
            key: 45,
            speed: 100,
            accent: 3,
            bright: 54,
            design: "fixedfm".into(),
            smooth: "soft".into(),
            diph: "glide".into(),
        }
    }
}

impl Settings {
    /// Keep saved or typed values inside what the workbench's controls allow.
    pub fn clamped(mut self) -> Self {
        self.key = self.key.clamp(KEY_MIN, KEY_MAX);
        self.speed = self.speed.clamp(60, 160);
        self.accent = self.accent.min(8);
        self.bright = self.bright.clamp(30, 75);
        self.text.truncate(self.text.char_indices().nth(2000).map_or(self.text.len(), |(i, _)| i));
        self
    }
}

#[derive(Serialize)]
struct Request<'a> {
    #[serde(flatten)]
    settings: &'a Settings,
    ch: u8,
    #[serde(rename = "fxCh")]
    fx_ch: u8,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Event {
    pub at_ms: f64,
    pub msg: Msg,
}

#[derive(Debug, Default, PartialEq)]
pub struct Phrase {
    pub events: Vec<Event>,
    pub notes: usize,
    pub parameters: usize,
}

impl Phrase {
    pub fn duration_ms(&self) -> f64 {
        self.events.last().map_or(0.0, |e| e.at_ms)
    }
}

#[derive(Deserialize)]
struct Compiled {
    /// `[time in ms, MIDI bytes...]`
    events: Vec<Vec<f64>>,
    lint: Vec<String>,
}

/// Turn the engine's output into a phrase, refusing anything that is not a well-formed stream:
/// the audio thread sends these bytes to hardware without looking at them again.
pub fn parse(json: &str) -> Result<(Phrase, Vec<String>), String> {
    let compiled: Compiled = serde_json::from_str(json).map_err(|e| format!("speech output: {e}"))?;
    let mut phrase = Phrase::default();
    for (index, raw) in compiled.events.iter().enumerate() {
        let bad = |why: &str| format!("speech event {index}: {why}");
        let (&at_ms, data) = raw.split_first().ok_or_else(|| bad("empty"))?;
        if !at_ms.is_finite() || at_ms < 0.0 {
            return Err(bad("time is not a positive number"));
        }
        if phrase.events.last().is_some_and(|e| e.at_ms > at_ms) {
            return Err(bad("out of order"));
        }
        if data.iter().any(|b| b.fract() != 0.0 || !(0.0..=255.0).contains(b)) {
            return Err(bad("a value is not a byte"));
        }
        let mut bytes = [0u8; 7];
        for (slot, value) in bytes.iter_mut().zip(data) {
            *slot = *value as u8;
        }
        let sysex = data.len() == 7 && bytes[0] == 0xF0 && bytes[6] == 0xF7;
        let short = data.len() == 3 && (0x80..0xF0).contains(&bytes[0]);
        if !(sysex || short) || bytes[1..data.len() - sysex as usize].iter().any(|b| *b > 127) {
            return Err(bad("not a MIDI message"));
        }
        phrase.notes += (bytes[0] & 0xF0 == 0x90) as usize;
        phrase.parameters += sysex as usize;
        phrase.events.push(Event { at_ms, msg: Msg { offset: 0, len: data.len() as u8, bytes } });
    }
    Ok((phrase, compiled.lint))
}

/// A phrase being played by the audio thread.
pub struct Player {
    phrase: Arc<Phrase>,
    next: usize,
    /// Host time of the phrase's time zero.
    start: u64,
    /// Notes started and not yet ended, to release if the phrase is cut short.
    sounding: [bool; 128],
}

impl Player {
    pub fn new(phrase: Arc<Phrase>, start: u64) -> Self {
        Player { phrase, next: 0, start, sounding: [false; 128] }
    }

    fn due(&self, index: usize, ticks_per_ms: f64) -> u64 {
        self.start + (self.phrase.events[index].at_ms * ticks_per_ms) as u64
    }

    /// Queue every message due before `horizon` (host ticks). Stops early if the queue is full
    /// and carries on from there next block.
    pub fn advance(
        &mut self,
        horizon: u64,
        ticks_per_ms: f64,
        send: &mut impl FnMut(u64, Msg) -> bool,
    ) {
        while self.next < self.phrase.events.len() {
            let when = self.due(self.next, ticks_per_ms);
            let msg = self.phrase.events[self.next].msg;
            if when > horizon || !send(when, msg) {
                break;
            }
            match msg.bytes[0] & 0xF0 {
                0x90 if msg.bytes[2] > 0 => self.sounding[msg.bytes[1] as usize] = true,
                0x80 | 0x90 => self.sounding[msg.bytes[1] as usize] = false,
                _ => {}
            }
            self.next += 1;
        }
    }

    /// Everything has been queued and its time has passed.
    pub fn finished(&self, now: u64, ticks_per_ms: f64) -> bool {
        let end = self.start + ((self.phrase.duration_ms() + TAIL_MS) * ticks_per_ms) as u64;
        self.next >= self.phrase.events.len() && now >= end
    }

    /// Cut the phrase short: release what is sounding, on whatever channel it was started.
    pub fn stop(&mut self, send: &mut impl FnMut(u64, Msg) -> bool) {
        let channel = self.phrase.events.iter().find(|e| e.msg.bytes[0] & 0xF0 == 0x90);
        let status = 0x80 | channel.map_or(0, |e| e.msg.bytes[0] & 0x0F);
        for (note, sounding) in self.sounding.iter_mut().enumerate() {
            if std::mem::take(sounding) {
                send(0, Msg { offset: 0, len: 3, bytes: [status, note as u8, 0, 0, 0, 0, 0] });
            }
        }
        self.next = self.phrase.events.len();
    }

    pub fn into_phrase(self) -> Arc<Phrase> {
        self.phrase
    }
}

/// The audio thread's side of speech: starts, feeds and ends phrases. No allocation, no waiting.
#[derive(Default)]
pub struct Runner {
    player: Option<Player>,
    /// A phrase waiting to start: (key, host time of its start, host time to give up at).
    pending: Option<(u8, u64, u64)>,
    /// Finished phrases go back to the speech thread through this, so they are freed there.
    trash: Option<rtrb::Producer<Arc<Phrase>>>,
}

/// What a block did, for the caller to act on.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Outcome {
    /// A phrase started: anything the caller has sounding should be released.
    pub started: bool,
    /// Speech ended and nothing follows: the unit holds the phrase's last voice, not the caller's.
    pub ended: bool,
}

impl Runner {
    pub fn connect(&mut self, shared: &Shared) {
        if self.trash.is_none() {
            self.trash = shared.take_trash();
        }
    }

    pub fn speaking(&self) -> bool {
        self.player.is_some()
    }

    /// Ask for the phrase to start at host time `at`, at the pitch of `key`. It starts on the
    /// next block, as soon as a compiled phrase is available, and is forgotten after two seconds.
    pub fn trigger(&mut self, key: u8, at: u64, ticks_per_ms: f64) {
        self.pending = Some((key, at, at + (2000.0 * ticks_per_ms) as u64));
    }

    fn discard(&mut self, player: Player) {
        if let Some(trash) = &mut self.trash {
            let _ = trash.push(player.into_phrase());
        }
    }

    /// Cut speech short. Returns true if a phrase was playing.
    pub fn stop(&mut self, send: &mut impl FnMut(u64, Msg) -> bool) -> bool {
        self.pending = None;
        let Some(mut player) = self.player.take() else { return false };
        player.stop(send);
        self.discard(player);
        true
    }

    /// Once per audio block: take the editor's requests, start a waiting phrase, queue what is
    /// due before `horizon`, and notice the end.
    pub fn block(
        &mut self,
        shared: &Shared,
        now: u64,
        horizon: u64,
        ticks_per_ms: f64,
        send: &mut impl FnMut(u64, Msg) -> bool,
    ) -> Outcome {
        let mut outcome = Outcome::default();
        if shared.stop.swap(false, Ordering::Relaxed) {
            outcome.ended = self.stop(send);
        }
        let request = shared.speak.swap(0, Ordering::AcqRel);
        if request & SPEAK != 0 {
            // The workbench also starts 60 ms ahead.
            self.trigger(request as u8 & 0x7F, now + (60.0 * ticks_per_ms) as u64, ticks_per_ms);
        }
        if let Some((key, at, give_up)) = self.pending {
            if let Some(phrase) = shared.phrase(key) {
                self.pending = None;
                if let Some(mut old) = self.player.take() {
                    old.stop(send);
                    self.discard(old);
                }
                self.player = Some(Player::new(phrase, at));
                (outcome.started, outcome.ended) = (true, false);
            } else if now > give_up {
                self.pending = None; // nothing was compiled: there is no phrase to speak
            }
        }
        if let Some(player) = &mut self.player {
            player.advance(horizon, ticks_per_ms, send);
            if player.finished(now, ticks_per_ms) {
                if let Some(player) = self.player.take() {
                    self.discard(player);
                }
                outcome.ended = true;
            }
        }
        shared.speaking.store(self.player.is_some(), Ordering::Relaxed);
        outcome
    }
}

#[derive(Clone, Debug, Default, PartialEq)]
pub enum State {
    #[default]
    Idle,
    Loading,
    Ready,
    Failed(String),
}

#[derive(Clone, Debug, Default)]
pub struct Status {
    pub state: State,
    /// (id, label) of the workbench's character presets, once the engine has loaded.
    pub characters: Vec<(String, String)>,
    pub lint: Vec<String>,
    pub summary: String,
    /// How many of the pitches are compiled.
    pub keys_ready: usize,
}

struct Job {
    settings: Settings,
    ch: u8,
    fx_ch: u8,
}

pub const SPEAK: u32 = 1 << 16;

/// Shared by the editor, the speech thread and the audio thread.
pub struct Shared {
    settings: Mutex<Settings>,
    phrases: Mutex<[Option<Arc<Phrase>>; KEYS]>,
    status: Mutex<Status>,
    jobs: Mutex<Option<Sender<Job>>>,
    /// Phrases the audio thread is done with come back here, so they are freed off that thread.
    trash: Mutex<(Option<rtrb::Producer<Arc<Phrase>>>, Option<rtrb::Consumer<Arc<Phrase>>>)>,
    /// Editor requests for the audio thread: `SPEAK | key`, or 0.
    pub speak: AtomicU32,
    pub stop: AtomicBool,
    /// Set by the audio thread while a phrase plays.
    pub speaking: AtomicBool,
    /// A phrase is compiled for at least one pitch. In speech mode without one, notes play the
    /// voice as usual rather than falling silent.
    pub available: AtomicBool,
}

impl Default for Shared {
    fn default() -> Self {
        let (producer, consumer) = rtrb::RingBuffer::new(8);
        Shared {
            settings: Mutex::default(),
            phrases: Mutex::new(std::array::from_fn(|_| None)),
            status: Mutex::default(),
            jobs: Mutex::new(None),
            trash: Mutex::new((Some(producer), Some(consumer))),
            speak: AtomicU32::new(0),
            stop: AtomicBool::new(false),
            speaking: AtomicBool::new(false),
            available: AtomicBool::new(false),
        }
    }
}

fn locked<T>(mutex: &Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(|poisoned| poisoned.into_inner())
}

impl Shared {
    pub fn settings(&self) -> Settings {
        locked(&self.settings).clone()
    }

    pub fn status(&self) -> Status {
        locked(&self.status).clone()
    }

    /// The phrase for `key` (clamped to the compiled range), or the nearest pitch compiled so
    /// far. Realtime-safe: never waits; `None` if the speech thread holds the lock or nothing is
    /// compiled yet.
    pub fn phrase(&self, key: u8) -> Option<Arc<Phrase>> {
        let phrases = self.phrases.try_lock().ok()?;
        let wanted = (key.clamp(KEY_MIN, KEY_MAX) - KEY_MIN) as usize;
        (0..KEYS)
            .filter(|i| phrases[*i].is_some())
            .min_by_key(|i| i.abs_diff(wanted))
            .and_then(|i| phrases[i].clone())
    }

    /// The audio thread's end of the queue that returns finished phrases. Taken once.
    pub fn take_trash(&self) -> Option<rtrb::Producer<Arc<Phrase>>> {
        locked(&self.trash).0.take()
    }

    /// Set the phrase and compile it. Starts the speech thread on first use.
    pub fn request(self: &Arc<Self>, settings: Settings, ch: u8, fx_ch: u8) {
        let settings = settings.clamped();
        *locked(&self.settings) = settings.clone();
        let mut jobs = locked(&self.jobs);
        if jobs.is_none() {
            let (sender, receiver) = mpsc::channel();
            let shared = self.clone();
            let consumer = locked(&self.trash).1.take();
            locked(&self.status).state = State::Loading;
            let spawned = std::thread::Builder::new()
                .name("fm1-speech".into())
                .stack_size(8 * 1024 * 1024)
                .spawn(move || worker(shared, receiver, consumer));
            if spawned.is_err() {
                locked(&self.status).state = State::Failed("could not start the speech thread".into());
                return;
            }
            *jobs = Some(sender);
        }
        if let Some(sender) = jobs.as_ref() {
            let _ = sender.send(Job { settings, ch, fx_ch });
        }
    }
}

/// The workbench's `app/` folder: the parent of a voice library folder, whether or not a
/// library has been built there.
pub fn app_dir() -> Option<PathBuf> {
    library::candidates()
        .into_iter()
        .filter_map(|library| library.parent().map(Path::to_path_buf))
        .find(|app| app.join("speech.js").is_file())
}

/// The JavaScript engine with the workbench's speech code loaded.
pub struct Engine {
    // Declared before `runtime`: the context must be dropped first.
    context: rquickjs::Context,
    _runtime: rquickjs::Runtime,
    pub characters: Vec<(String, String)>,
}

impl Engine {
    pub fn load(app: &Path) -> Result<Engine, String> {
        let read = |name: &str| {
            std::fs::read_to_string(app.join(name)).map_err(|e| format!("{}: {e}", app.join(name).display()))
        };
        let runtime = rquickjs::Runtime::new().map_err(|e| e.to_string())?;
        runtime.set_max_stack_size(4 * 1024 * 1024);
        let context = rquickjs::Context::full(&runtime).map_err(|e| e.to_string())?;
        let characters = context.with(|ctx| -> Result<String, String> {
            for (name, source) in [
                ("dx7.js", read("dx7.js")?),
                ("speech.js", read("speech.js")?),
                ("speech_glue.js", include_str!("speech_glue.js").to_string()),
            ] {
                ctx.eval::<(), _>(source).map_err(|e| format!("{name}: {}", explain(&ctx, e)))?;
            }
            let load: rquickjs::Function =
                ctx.globals().get("fm1SpeechLoad").map_err(|e| explain(&ctx, e))?;
            let data = (read("speech/units.json")?, read("speech/cmudict.txt")?, read("speech/pos.json")?);
            load.call(data).map_err(|e| explain(&ctx, e))
        })?;
        let characters = serde_json::from_str(&characters).map_err(|e| e.to_string())?;
        Ok(Engine { context, _runtime: runtime, characters })
    }

    pub fn compile(&self, settings: &Settings, ch: u8, fx_ch: u8) -> Result<(Phrase, Vec<String>), String> {
        let request = serde_json::to_string(&Request { settings, ch, fx_ch }).map_err(|e| e.to_string())?;
        let output = self.context.with(|ctx| -> Result<String, String> {
            let compile: rquickjs::Function =
                ctx.globals().get("fm1SpeechCompile").map_err(|e| explain(&ctx, e))?;
            compile.call((request,)).map_err(|e| explain(&ctx, e))
        })?;
        parse(&output)
    }
}

fn explain(ctx: &rquickjs::Ctx, error: rquickjs::Error) -> String {
    let caught = ctx.catch();
    match caught.as_exception() {
        Some(e) => format!("{} {}", e.message().unwrap_or_default(), e.stack().unwrap_or_default()),
        None => error.to_string(),
    }
}

fn worker(shared: Arc<Shared>, jobs: Receiver<Job>, mut trash: Option<rtrb::Consumer<Arc<Phrase>>>) {
    let engine = app_dir()
        .ok_or_else(|| "The workbench's app/speech.js was not found (set FM1_WORKBENCH).".to_string())
        .and_then(|app| Engine::load(&app));
    let engine = match engine {
        Ok(engine) => engine,
        Err(error) => {
            locked(&shared.status).state = State::Failed(error);
            // Keep taking jobs so that senders never block; there is nothing to do with them.
            for _ in jobs {}
            return;
        }
    };
    locked(&shared.status).characters = engine.characters.clone();

    let mut pending = jobs.recv().ok();
    while let Some(mut job) = pending.take() {
        while let Ok(newer) = jobs.try_recv() {
            job = newer; // only the latest text matters
        }
        if let Some(trash) = trash.as_mut() {
            while trash.pop().is_ok() {}
        }
        shared.available.store(false, Ordering::Release);
        *locked(&shared.phrases) = std::array::from_fn(|_| None);
        {
            let mut status = locked(&shared.status);
            (status.state, status.keys_ready) = (State::Loading, 0);
        }
        if job.settings.text.trim().is_empty() {
            let mut status = locked(&shared.status);
            (status.state, status.summary) = (State::Ready, "Type something to say.".into());
            status.lint.clear();
        } else {
            // The chosen pitch first, then outwards from it; a newer job interrupts.
            let base = job.settings.key;
            let mut keys: Vec<u8> = (KEY_MIN..=KEY_MAX).collect();
            keys.sort_by_key(|key| key.abs_diff(base));
            for (done, key) in keys.into_iter().enumerate() {
                let settings = Settings { key, ..job.settings.clone() };
                match engine.compile(&settings, job.ch, job.fx_ch) {
                    Ok((phrase, lint)) => {
                        let mut status = locked(&shared.status);
                        if key == base {
                            status.summary = format!(
                                "{} notes · {:.1} s · {} parameter changes",
                                phrase.notes,
                                phrase.duration_ms() / 1000.0,
                                phrase.parameters
                            );
                            status.lint = lint;
                            status.state = State::Ready;
                        }
                        status.keys_ready = done + 1;
                        drop(status);
                        locked(&shared.phrases)[(key - KEY_MIN) as usize] = Some(Arc::new(phrase));
                        shared.available.store(true, Ordering::Release);
                    }
                    Err(error) => {
                        locked(&shared.status).state = State::Failed(error);
                        break;
                    }
                }
                if let Ok(newer) = jobs.try_recv() {
                    pending = Some(newer);
                    break;
                }
            }
        }
        if pending.is_none() {
            pending = jobs.recv().ok();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn collect(player: &mut Player, horizon: u64) -> Vec<(u64, Vec<u8>)> {
        let mut out = Vec::new();
        player.advance(horizon, 1000.0, &mut |when, msg| {
            out.push((when, msg.data().to_vec()));
            true
        });
        out
    }

    const SAMPLE: &str = r#"{"events": [[0, 240, 67, 16, 0, 5, 99, 247], [40.5, 144, 45, 110],
        [68.5, 128, 45, 0], [70, 176, 0, 1], [90, 144, 57, 110], [118, 128, 57, 0]],
        "notes": 2, "lint": ["\"FM\" spelled out as letters"]}"#;

    #[test]
    fn engine_output_becomes_a_phrase_of_timed_messages() {
        let (phrase, lint) = parse(SAMPLE).unwrap();
        assert_eq!((phrase.events.len(), phrase.notes, phrase.parameters), (6, 2, 1));
        assert_eq!(phrase.events[0].msg.data(), &[0xF0, 0x43, 0x10, 0, 5, 99, 0xF7]);
        assert_eq!(phrase.events[1], Event { at_ms: 40.5, msg: Msg { offset: 0, len: 3, bytes: [0x90, 45, 110, 0, 0, 0, 0] } });
        assert_eq!(phrase.duration_ms(), 118.0);
        assert_eq!(lint, vec!["\"FM\" spelled out as letters"]);
    }

    #[test]
    fn malformed_engine_output_never_reaches_the_hardware() {
        for (events, why) in [
            ("[[0, 144, 45]]", "short message"),
            ("[[0, 144, 200, 1]]", "data byte above 127"),
            ("[[0, 240, 67, 16, 0, 5, 99, 1]]", "SysEx without its end"),
            ("[[0, 45, 1, 2]]", "no status byte"),
            ("[[5, 144, 45, 1], [4, 128, 45, 0]]", "out of order"),
            ("[[-1, 144, 45, 1]]", "negative time"),
            ("[[0, 144.5, 45, 1]]", "fractional byte"),
            ("[[0, 247, 1, 2]]", "system message"),
            ("[[]]", "empty event"),
        ] {
            let json = format!(r#"{{"events": {events}, "lint": []}}"#);
            assert!(parse(&json).is_err(), "{why} was accepted");
        }
        assert!(parse("not json").is_err());
        assert_eq!(parse(r#"{"events": [], "lint": []}"#).unwrap().0.events.len(), 0);
    }

    #[test]
    fn player_queues_what_is_due_and_resumes_when_the_queue_fills() {
        let phrase = Arc::new(parse(SAMPLE).unwrap().0);
        let mut player = Player::new(phrase.clone(), 1_000_000);
        // 1000 ticks per ms: the first block reaches 41 ms into the phrase.
        let first = collect(&mut player, 1_041_000);
        assert_eq!(first.iter().map(|e| e.0).collect::<Vec<_>>(), vec![1_000_000, 1_040_500]);
        assert!(!player.finished(1_041_000, 1000.0));

        // A full queue stops it; nothing is skipped.
        let mut room = 1;
        player.advance(2_000_000, 1000.0, &mut |_, _| {
            room -= 1;
            room >= 0
        });
        let rest = collect(&mut player, 2_000_000);
        assert_eq!(rest.len(), 3);
        assert_eq!(rest[0].1, vec![0xB0, 0, 1]);
        assert!(!player.finished(1_200_000, 1000.0)); // queued, but its tail has not passed
        assert!(player.finished(1_218_000, 1000.0));
    }

    #[test]
    fn stopping_releases_only_the_note_still_sounding() {
        let phrase = Arc::new(parse(SAMPLE).unwrap().0);
        let mut player = Player::new(phrase, 0);
        collect(&mut player, 95_000); // up to the second note-on
        let mut released = Vec::new();
        player.stop(&mut |when, msg| {
            released.push((when, msg.data().to_vec()));
            true
        });
        assert_eq!(released, vec![(0, vec![0x80, 57, 0])]);
        assert!(collect(&mut player, u64::MAX).is_empty());
    }

    /// A `Shared` holding `SAMPLE` at one pitch, without the engine or its thread.
    fn shared_with_phrase() -> Shared {
        let shared = Shared::default();
        locked(&shared.phrases)[(45 - KEY_MIN) as usize] = Some(Arc::new(parse(SAMPLE).unwrap().0));
        shared
    }

    #[test]
    fn runner_speaks_on_request_retriggers_stops_and_reports_the_end() {
        let shared = shared_with_phrase();
        let mut runner = Runner::default();
        runner.connect(&shared);
        let sent = std::cell::RefCell::new(Vec::<(u64, Vec<u8>)>::new());
        let run = |runner: &mut Runner, now: u64| {
            runner.block(&shared, now, now + 10_000, 1000.0, &mut |when, msg| {
                sent.borrow_mut().push((when, msg.data().to_vec()));
                true
            })
        };
        assert_eq!(run(&mut runner, 0), Outcome::default());

        // The editor's Speak button: starts 60 ms ahead, at the requested pitch's phrase.
        shared.speak.store(SPEAK | 45, Ordering::Release);
        assert_eq!(run(&mut runner, 1_000_000), Outcome { started: true, ended: false });
        assert!(runner.speaking() && shared.speaking.load(Ordering::Relaxed));
        assert!(sent.borrow().is_empty()); // nothing is due within 10 ms of now
        run(&mut runner, 1_055_000);
        assert_eq!(*sent.borrow(), vec![(1_060_000, vec![0xF0, 0x43, 0x10, 0, 5, 99, 0xF7])]);

        // A note retriggers mid-phrase: the sounding note is released, the phrase starts over.
        run(&mut runner, 1_095_000); // first note-on is out
        sent.borrow_mut().clear();
        runner.trigger(45, 1_100_000, 1000.0);
        assert_eq!(run(&mut runner, 1_098_000), Outcome { started: true, ended: false });
        assert_eq!(sent.borrow()[0], (0, vec![0x80, 45, 0]));
        assert_eq!(sent.borrow()[1].0, 1_100_000);

        // Running to the end reports it once, and the phrase goes back to the speech thread.
        assert_eq!(run(&mut runner, 1_400_000), Outcome { started: false, ended: true });
        assert!(!runner.speaking() && !shared.speaking.load(Ordering::Relaxed));
        assert_eq!(run(&mut runner, 1_500_000), Outcome::default());

        // Stop from the editor while speaking.
        runner.trigger(45, 2_000_000, 1000.0);
        run(&mut runner, 2_045_000);
        shared.stop.store(true, Ordering::Relaxed);
        assert_eq!(run(&mut runner, 2_050_000), Outcome { started: false, ended: true });
        assert!(!runner.speaking());
    }

    #[test]
    fn runner_waits_briefly_for_a_phrase_then_gives_up() {
        let empty = Shared::default();
        let mut runner = Runner::default();
        let mut nothing = |_: u64, _: Msg| -> bool { panic!("nothing to send") };
        runner.trigger(45, 1_000_000, 1000.0);
        assert_eq!(runner.block(&empty, 1_000_000, 1_010_000, 1000.0, &mut nothing), Outcome::default());
        // The phrase arrives late (still compiling when the note came): it starts, timed from
        // the note, so its first messages are sent as overdue rather than shifted.
        let shared = shared_with_phrase();
        let mut sent = Vec::new();
        let outcome = runner.block(&shared, 1_500_000, 1_510_000, 1000.0, &mut |when, _| {
            sent.push(when);
            true
        });
        assert!(outcome.started && sent[0] == 1_000_000);

        let mut late = Runner::default();
        late.trigger(45, 1_000_000, 1000.0);
        late.block(&empty, 3_100_000, 3_110_000, 1000.0, &mut nothing);
        assert_eq!(late.block(&shared, 3_200_000, 3_210_000, 1000.0, &mut nothing), Outcome::default());
    }

    #[test]
    fn settings_survive_a_project_and_stay_within_the_controls() {
        let saved = Settings { text: "Hi".into(), character: "robot".into(), key: 50, ..Settings::default() };
        let back: Settings = serde_json::from_str(&serde_json::to_string(&saved).unwrap()).unwrap();
        assert_eq!(back, saved);
        let old: Settings = serde_json::from_str(r#"{"text": "from an older project"}"#).unwrap();
        assert_eq!((old.key, old.smooth.as_str()), (45, "soft"));
        let wild = Settings { key: 200, speed: 5, accent: 99, bright: 0, text: "x".repeat(5000), ..saved };
        let tame = wild.clamped();
        assert_eq!((tame.key, tame.speed, tame.accent, tame.bright, tame.text.len()), (60, 60, 8, 30, 2000));
        let request = serde_json::to_value(Request { settings: &tame, ch: 0, fx_ch: 1 }).unwrap();
        assert_eq!((request["fxCh"].as_u64(), request["key"].as_u64()), (Some(1), Some(60)));
    }

    /// The real engine, when the workbench is on this machine.
    fn workbench() -> Option<Engine> {
        let app = app_dir()?;
        Some(Engine::load(&app).unwrap_or_else(|e| panic!("{e}")))
    }

    #[test]
    fn workbench_engine_speaks_a_phrase_at_each_pitch() {
        let Some(engine) = workbench() else {
            eprintln!("workbench speech engine not present; skipped");
            return;
        };
        assert!(engine.characters.iter().any(|(id, label)| id == "natural" && label == "Natural"));
        assert!(engine.characters.len() >= 10);
        let settings = Settings { text: "Hello there. Can you hear me?".into(), ..Settings::default() };
        let (phrase, lint) = engine.compile(&settings, 0, 1).unwrap();
        assert!(phrase.notes > 40 && phrase.parameters > 200, "{} notes", phrase.notes);
        assert!(phrase.duration_ms() > 1500.0 && phrase.duration_ms() < 6000.0);
        // The device is assumed unknown: the first note is preceded by a whole voice.
        let first_note = phrase.events.iter().position(|e| e.msg.bytes[0] == 0x90).unwrap();
        assert!(first_note >= 146, "only {first_note} parameters before the first note");
        assert!(lint.iter().any(|l| l.contains("question")), "{lint:?}");

        let higher = engine.compile(&Settings { key: 57, ..settings.clone() }, 0, 1).unwrap().0;
        let pitch = |p: &Phrase| p.events.iter().find(|e| e.msg.bytes[0] == 0x90).map(|e| e.msg.bytes[1]);
        assert_eq!(pitch(&higher).unwrap() as i32 - pitch(&phrase).unwrap() as i32, 12);

        // Effects characters use the effect channel and switch their effects off at the end.
        let radio = engine.compile(&Settings { character: "radio".into(), ..settings }, 0, 1).unwrap().0;
        let fx: Vec<_> = radio.events.iter().filter(|e| e.msg.bytes[0] == 0xB1).collect();
        assert!(fx.len() >= 4 && fx.last().unwrap().msg.bytes[2] == 0);
    }

    /// Node runs the same files: the embedded engine must produce the same events.
    #[test]
    fn embedded_engine_matches_node() {
        let (Some(engine), Some(app)) = (workbench(), app_dir()) else { return };
        let node = std::process::Command::new("node").arg("--version").output();
        if !node.is_ok_and(|o| o.status.success()) {
            eprintln!("node not installed; skipped");
            return;
        }
        let script = r#"
            const fs = require('fs'), path = require('path'), vm = require('vm');
            const [app, glue, request] = process.argv.slice(1);
            const ctx = { Math }; vm.createContext(ctx);
            for (const f of ['dx7.js', 'speech.js']) vm.runInContext(fs.readFileSync(path.join(app, f), 'utf8'), ctx);
            vm.runInContext(fs.readFileSync(glue, 'utf8'), ctx);
            const rd = f => fs.readFileSync(path.join(app, 'speech', f), 'utf8');
            Object.assign(ctx, { u: rd('units.json'), d: rd('cmudict.txt'), p: rd('pos.json'), q: request });
            vm.runInContext('fm1SpeechLoad(u, d, p)', ctx);
            console.log(vm.runInContext('fm1SpeechCompile(q)', ctx));"#;
        let glue = Path::new(env!("CARGO_MANIFEST_DIR")).join("src/speech_glue.js");
        for (text, character, smooth, diph, design) in [
            ("The quick brown fox jumps over 13 lazy dogs.", "natural", "soft", "glide", "fixedfm"),
            ("Why? Dr. Smith paid $5.50 in 1999!", "feminine", "smooth", "all", "harmonic"),
            ("I am a robot. Take me to your leader.", "robot", "sync", "off", "lebrun"),
            ("Boy, the cathedral is loud tonight.", "cathedral", "off", "glide", "sine"),
        ] {
            let settings = Settings {
                text: text.into(),
                character: character.into(),
                smooth: smooth.into(),
                diph: diph.into(),
                design: design.into(),
                ..Settings::default()
            };
            let request = serde_json::to_string(&Request { settings: &settings, ch: 0, fx_ch: 1 }).unwrap();
            let output = std::process::Command::new("node")
                .args(["-e", script])
                .arg(&app)
                .arg(&glue)
                .arg(&request)
                .output()
                .unwrap();
            assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
            let (reference, _) = parse(&String::from_utf8_lossy(&output.stdout)).unwrap();
            let (ours, _) = engine.compile(&settings, 0, 1).unwrap();
            assert_eq!(ours.events.len(), reference.events.len(), "{character}");
            for (a, b) in ours.events.iter().zip(&reference.events) {
                assert!(a.msg == b.msg && (a.at_ms - b.at_ms).abs() < 1e-6, "{character}: {a:?} vs {b:?}");
            }
        }
    }

    #[test]
    fn shared_compiles_on_its_own_thread_and_serves_the_nearest_pitch() {
        if app_dir().is_none() {
            return;
        }
        let shared = Arc::new(Shared::default());
        assert!(shared.phrase(45).is_none());
        shared.request(Settings { text: "Hello.".into(), key: 48, ..Settings::default() }, 0, 1);
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(30);
        while shared.status().keys_ready < KEYS {
            assert!(std::time::Instant::now() < deadline, "{:?}", shared.status());
            std::thread::sleep(std::time::Duration::from_millis(20));
        }
        let status = shared.status();
        assert_eq!(status.state, State::Ready);
        assert!(status.summary.contains("notes"), "{}", status.summary);
        let note = |key| {
            let phrase = shared.phrase(key).unwrap();
            phrase.events.iter().find(|e| e.msg.bytes[0] == 0x90).unwrap().msg.bytes[1]
        };
        assert_eq!(note(60) as i32 - note(48) as i32, 12);
        assert_eq!(note(100), note(60)); // beyond the range: the highest compiled pitch
        assert_eq!(note(0), note(36));

        // An empty text clears the phrase rather than leaving the old one playable.
        assert!(shared.available.load(Ordering::Acquire));
        shared.request(Settings::default(), 0, 1);
        while shared.status().summary != "Type something to say." {
            assert!(std::time::Instant::now() < deadline);
            std::thread::sleep(std::time::Duration::from_millis(10));
        }
        assert!(shared.phrase(48).is_none() && !shared.available.load(Ordering::Acquire));
    }
}
