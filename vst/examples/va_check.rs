//! Hardware check for the FM-1+VA firmware controllers: hold a note on a vibrato voice with a
//! given LFO speed and master volume, sent the way the plugin sends them.
//!
//! usage: va_check [LFO speed 0-99] [volume 0-127]

use std::sync::atomic::Ordering;
use std::time::{Duration, Instant};

use fm1_vst::dx7::{self, global, op, op_index};
use fm1_vst::engine::Engine;
use fm1_vst::midi_out::MidiOut;

fn main() {
    let mut args = std::env::args().skip(1).map(|a| a.parse::<u8>().unwrap_or(0));
    let (speed, volume) = (args.next().unwrap_or(35), args.next().unwrap_or(100));
    let mut out = MidiOut::start(Default::default());
    let deadline = Instant::now() + Duration::from_secs(3);
    while !out.link.connected.load(Ordering::Relaxed) {
        assert!(Instant::now() < deadline, "FM-1 not found");
        std::thread::sleep(Duration::from_millis(10));
    }
    // A plain sine whose pitch the LFO modulates: the vibrato rate shows the LFO speed. (The
    // workbench measured the firmware this way; the tremolo rate does not follow CC 76.)
    let mut voice = dx7::init_voice();
    voice[op_index(1, op::R4)] = 80;
    voice[global::LFS] = speed.min(99);
    voice[138] = 0; // LFO delay
    voice[global::LPMD] = 99;
    voice[global::LPMS] = 4;
    let mut engine = Engine::default();
    engine.sync_voice(&voice, 0, 0, &mut |m| out.push(0, m));
    engine.sync_lfo(&voice, 0, 0, &mut |m| out.push(0, m));
    engine.sync_volume(volume, 0, &mut |m| out.push(0, m));
    std::thread::sleep(Duration::from_millis(300));
    engine.note_on(69, 100, 0, 0, 0, &mut |m| out.push(0, m));
    std::thread::sleep(Duration::from_millis(2600));
    engine.release_all(0, &mut |m| out.push(0, m));
}
