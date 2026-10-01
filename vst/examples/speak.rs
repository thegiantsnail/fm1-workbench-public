//! Hardware check for speech: compile a phrase with the workbench's engine and speak it on the
//! FM-1 through the same runner the plugin's audio thread uses.
//!
//! usage: speak "<text>" [character] [key] [stop after ms]

use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::{Duration, Instant};

use fm1_vst::midi_out::{clock, MidiOut};
use fm1_vst::speech::{Runner, Settings, Shared, State};

fn main() {
    let mut args = std::env::args().skip(1);
    let text = args.next().expect("text to speak");
    let character = args.next().unwrap_or_else(|| "natural".into());
    let key = args.next().and_then(|k| k.parse().ok()).unwrap_or(45u8);
    let stop_after = args.next().and_then(|ms| ms.parse::<u64>().ok());

    let shared = Arc::new(Shared::default());
    shared.request(Settings { text, character, key, ..Settings::default() }, 0, 1);
    let deadline = Instant::now() + Duration::from_secs(20);
    while shared.status().keys_ready == 0 {
        if let State::Failed(error) = shared.status().state {
            eprintln!("{error}");
            std::process::exit(1);
        }
        assert!(Instant::now() < deadline, "the phrase did not compile");
        std::thread::sleep(Duration::from_millis(10));
    }
    let status = shared.status();
    println!("{}", status.summary);
    for note in &status.lint {
        println!("  {note}");
    }

    let mut out = MidiOut::start(Default::default());
    let deadline = Instant::now() + Duration::from_secs(3);
    while !out.link.connected.load(Ordering::Relaxed) {
        assert!(Instant::now() < deadline, "FM-1 not found");
        std::thread::sleep(Duration::from_millis(10));
    }
    let ticks_per_ms = clock::rate() / 1000.0;
    let mut runner = Runner::default();
    runner.connect(&shared);
    let begun = Instant::now();
    runner.trigger(key, clock::now() + (60.0 * ticks_per_ms) as u64, ticks_per_ms);
    loop {
        // Stand in for the plugin's audio blocks: about 3 ms each.
        let now = clock::now();
        let horizon = now + (9.0 * ticks_per_ms) as u64;
        let outcome = runner.block(&shared, now, horizon, ticks_per_ms, &mut |when, msg| out.push(when, msg));
        if outcome.ended {
            break;
        }
        if stop_after.is_some_and(|ms| begun.elapsed() >= Duration::from_millis(ms)) {
            shared.stop.store(true, Ordering::Relaxed);
        }
        std::thread::sleep(Duration::from_millis(3));
    }
    let link = out.link.clone();
    drop(out);
    println!(
        "spoke for {:.2} s; sent {} dropped {}",
        begun.elapsed().as_secs_f64(),
        link.sent.load(Ordering::Relaxed),
        link.dropped.load(Ordering::Relaxed)
    );
}
