//! Hardware check for shutdown: start a note on the FM-1, then release and drop the output at
//! once. The note must stop, which requires the queued note-off to be sent during the drop.

use std::sync::atomic::Ordering;
use std::time::{Duration, Instant};

use fm1_vst::dx7;
use fm1_vst::engine::Engine;
use fm1_vst::midi_out::MidiOut;

fn main() {
    let mut out = MidiOut::start(Default::default());
    let deadline = Instant::now() + Duration::from_secs(3);
    while !out.link.connected.load(Ordering::Relaxed) {
        if Instant::now() > deadline {
            eprintln!("FM-1 not found");
            std::process::exit(1);
        }
        std::thread::sleep(Duration::from_millis(10));
    }
    let mut engine = Engine::default();
    let sent = engine.sync_voice(&dx7::init_voice(), 0, 0, &mut |msg| out.push(0, msg));
    engine.note_on(57, 100, 0, 0, 0, &mut |msg| out.push(0, msg));
    println!("voice parameters sent: {sent}, holding a note");
    std::thread::sleep(Duration::from_millis(1500));
    engine.release_all(0, &mut |msg| out.push(0, msg));
    let link = out.link.clone();
    drop(out);
    println!(
        "dropped; sent {} dropped {}",
        link.sent.load(Ordering::Relaxed),
        link.dropped.load(Ordering::Relaxed)
    );
}
