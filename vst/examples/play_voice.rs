//! Hardware check for voice integrity: send a library voice in one go, as the plugin does when a
//! voice is loaded, and hold a note on it.
//!
//! usage: play_voice <library index> [note]

use std::sync::atomic::Ordering;
use std::time::{Duration, Instant};

use fm1_vst::engine::Engine;
use fm1_vst::library::{candidates, Library};
use fm1_vst::midi_out::MidiOut;

fn main() {
    let mut args = std::env::args().skip(1).map(|a| a.parse::<usize>().unwrap_or(0));
    let (index, note) = (args.next().unwrap_or(0), args.next().unwrap_or(60) as u8);
    let dir = candidates().into_iter().find(|d| d.join("index.json").is_file()).expect("no library");
    let library = Library::load(&dir).expect("library");
    let entry = &library.voices[index.min(library.voices.len() - 1)];
    let voice = entry.voice();

    let mut out = MidiOut::start(Default::default());
    let deadline = Instant::now() + Duration::from_secs(3);
    while !out.link.connected.load(Ordering::Relaxed) {
        assert!(Instant::now() < deadline, "FM-1 not found");
        std::thread::sleep(Duration::from_millis(10));
    }
    let mut engine = Engine::default();
    let sent = engine.sync_voice(&voice, 0, 0, &mut |m| out.push(0, m));
    // The note follows the voice at once, as when a host plays the first note after a load.
    engine.note_on(note, 100, Engine::shift(&voice), 0, 0, &mut |m| out.push(0, m));
    println!("{} ({sent} parameters)", entry.name());
    std::thread::sleep(Duration::from_millis(1200));
    engine.release_all(0, &mut |m| out.push(0, m));
}
