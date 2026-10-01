//! Hardware check for kit mode: play a library kit's tracks in turn, a voice switch on every hit,
//! using the same engine calls the plugin makes. Prints the schedule so a recording can be scored.
//!
//! usage: kit_play [kit index] [hits] [step ms] [lookahead ms]

use std::sync::atomic::Ordering;
use std::time::{Duration, Instant};

use fm1_vst::engine::{kit_schedule, Engine};
use fm1_vst::library::{candidates, Library};
use fm1_vst::midi_out::{clock, MidiOut};

fn main() {
    let mut args = std::env::args().skip(1).map(|a| a.parse::<usize>().unwrap_or(0));
    let (kit, hits, step_ms) = (args.next().unwrap_or(0), args.next().unwrap_or(16), args.next().unwrap_or(150));
    let lead_ms = args.next().unwrap_or(20);
    let dir = candidates().into_iter().find(|d| d.join("index.json").is_file()).expect("no library");
    let library = Library::load(&dir).expect("library");
    let kit = &library.kits[kit.min(library.kits.len() - 1)];
    let table = kit.table();

    let mut out = MidiOut::start(Default::default());
    let deadline = Instant::now() + Duration::from_secs(3);
    while !out.link.connected.load(Ordering::Relaxed) {
        assert!(Instant::now() < deadline, "FM-1 not found");
        std::thread::sleep(Duration::from_millis(10));
    }
    let mut engine = Engine::default();
    let first = table.slots[kit.tracks[0].key as usize];
    let sent = engine.sync_voice(&first.voice, 0, 0, &mut |m| out.push(0, m));
    println!("kit {:?}: {} tracks; initial voice {} messages", kit.name, kit.tracks.len(), sent);
    std::thread::sleep(Duration::from_millis(600));

    // Schedule every hit against the host clock, as the plugin does from a block's timestamps.
    let rate = clock::rate();
    let start = clock::now() + (rate * 0.2) as u64;
    let ticks = |ms: usize| (rate * ms as f64 / 1000.0) as i64;
    let mut last_note = i64::MIN / 2;
    for hit in 0..hits {
        let track = &kit.tracks[hit % kit.tracks.len()];
        let slot = table.slots[track.key as usize];
        let when = start + ticks(hit * step_ms) as u64;
        // Host ticks stand in for the plugin's sample offsets.
        let (change, note) = kit_schedule(ticks(hit * step_ms), ticks(lead_ms), last_note);
        last_note = note;
        let (change, note) = (start + change as u64, start + note as u64);
        let switched = engine.sync_voice(&slot.voice, 0, 0, &mut |m| out.push(change, m));
        let pitch = slot.note as i32 + Engine::shift(&slot.voice);
        engine.note_on_as(track.key, pitch, 110, 0, 0, &mut |m| out.push(note, m));
        let off = note + (rate * 0.06) as u64;
        engine.note_off(track.key, 0, 0, &mut |m| out.push(off, m));
        println!("hit {hit} {} params {switched}", track.name);
        // Queue each hit shortly before it is due, as a host block would.
        let due = Duration::from_secs_f64((when - clock::now().min(when)) as f64 / rate);
        std::thread::sleep(due.saturating_sub(Duration::from_millis(20)));
    }
    std::thread::sleep(Duration::from_millis(600));
    engine.release_all(0, &mut |m| out.push(0, m));
    let link = out.link.clone();
    drop(out);
    println!("sent {} dropped {}", link.sent.load(Ordering::Relaxed), link.dropped.load(Ordering::Relaxed));
}
