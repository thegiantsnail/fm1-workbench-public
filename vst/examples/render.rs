//! Render a voice on the built-in synth to a WAV file, with no host and no hardware: the same
//! messages the plugin would send to the FM-1 (the whole voice, then notes) go to the synth.
//!
//! usage: render <out.wav> [library index] [note] [seconds held] [va]
//!
//! Without a library index, or without a built library, the init voice is used. A final `va`
//! renders as Baud Girl's FM-1+VA firmware rather than stock M-VAVE.

use std::io::Write;
use std::time::Instant;

use fm1_vst::dx7;
use fm1_vst::engine::{Engine, Msg};
use fm1_vst::library::{candidates, Library};
use fm1_vst::synth::Synth;

const SR: u32 = 44100;

fn main() -> std::io::Result<()> {
    let mut args = std::env::args().skip(1);
    let path = args.next().expect("usage: render <out.wav> [library index] [note] [seconds held] [va]");
    let index = args.next().and_then(|a| a.parse::<usize>().ok());
    let note = args.next().and_then(|a| a.parse::<u8>().ok()).unwrap_or(60);
    let held = args.next().and_then(|a| a.parse::<f32>().ok()).unwrap_or(1.0);
    let va = args.next().is_some_and(|a| a == "va");

    let library = candidates().into_iter().find_map(|dir| Library::load(&dir).ok());
    let (name, voice) = match (index, &library) {
        (Some(index), Some(library)) => {
            let entry = &library.voices[index.min(library.voices.len() - 1)];
            (entry.name().to_string(), entry.voice())
        }
        _ => ("INIT VOICE".to_string(), dx7::init_voice()),
    };

    let mut synth = Synth::new(SR as f32);
    synth.set_firmware_va(va);
    let mut engine = Engine::default();
    let off = (held * SR as f32) as u32;
    let mut events: Vec<Msg> = Vec::new();
    engine.sync_voice(&voice, 0, 0, &mut |msg| {
        events.push(msg);
        true
    });
    if va {
        engine.sync_lfo(&voice, 0, 0, &mut |msg| {
            events.push(msg);
            true
        });
    }
    engine.note_on(note, 100, Engine::shift(&voice), 0, 0, &mut |msg| {
        events.push(msg);
        true
    });
    engine.note_off(note, 0, off, &mut |msg| {
        events.push(msg);
        true
    });
    for msg in &events {
        synth.queue(msg.offset as u64, msg);
    }

    let mut samples = vec![0f32; (SR as f32 * (held + 1.5)) as usize];
    let started = Instant::now();
    for block in samples.chunks_mut(512) {
        synth.render(block);
    }
    let took = started.elapsed().as_secs_f64();
    let seconds = samples.len() as f64 / SR as f64;
    let peak = samples.iter().fold(0f32, |peak, s| peak.max(s.abs()));
    println!(
        "{name}: note {note}, {seconds:.1} s rendered in {:.1} ms ({:.0}x real time), peak {:.1} dBFS",
        took * 1000.0,
        seconds / took,
        20.0 * peak.max(1e-12).log10()
    );

    // 16-bit mono PCM.
    let data: Vec<u8> = samples.iter().flat_map(|s| ((s * 32767.0) as i16).to_le_bytes()).collect();
    let mut file = std::fs::File::create(&path)?;
    file.write_all(b"RIFF")?;
    file.write_all(&(36 + data.len() as u32).to_le_bytes())?;
    file.write_all(b"WAVEfmt ")?;
    file.write_all(&16u32.to_le_bytes())?;
    file.write_all(&1u16.to_le_bytes())?; // PCM
    file.write_all(&1u16.to_le_bytes())?; // mono
    file.write_all(&SR.to_le_bytes())?;
    file.write_all(&(SR * 2).to_le_bytes())?;
    file.write_all(&2u16.to_le_bytes())?;
    file.write_all(&16u16.to_le_bytes())?;
    file.write_all(b"data")?;
    file.write_all(&(data.len() as u32).to_le_bytes())?;
    file.write_all(&data)
}
