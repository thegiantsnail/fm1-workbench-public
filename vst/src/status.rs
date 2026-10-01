//! A status file for looking inside the plugin while a host has it loaded.
//!
//! Off unless a file named `debug` exists in the plugin's support folder (`platform::support_dir`:
//! `~/Library/Application Support/FM-1 Controller/` on macOS, `%APPDATA%\FM-1 Controller\` on Windows).
//! While it does, `status.json` beside it is rewritten once a second by a background thread with
//! what the plugin believes: whether the unit is connected, the modes, the speech engine's state
//! and counters that show the host is calling the audio callback and delivering notes.

use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::thread::JoinHandle;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde_json::json;

use crate::params::Fm1Params;

pub fn folder() -> Option<PathBuf> {
    crate::platform::support_dir()
}

/// What the plugin believes, as JSON.
pub fn snapshot(params: &Fm1Params) -> serde_json::Value {
    let shared = &params.shared;
    let speech = shared.speech.status();
    let settings = shared.speech.settings();
    json!({
        "written_at": SystemTime::now().duration_since(UNIX_EPOCH).map_or(0, |d| d.as_secs()),
        "version": env!("CARGO_PKG_VERSION"),
        "audio": {
            "blocks": shared.blocks.load(Ordering::Relaxed),
            "notes_received": shared.notes.load(Ordering::Relaxed),
        },
        "midi": {
            "connected": shared.link.connected.load(Ordering::Relaxed),
            "sent": shared.link.sent.load(Ordering::Relaxed),
            "dropped": shared.link.dropped.load(Ordering::Relaxed),
        },
        "sound": {
            "mode": crate::params::Sound::NAMES[params.sound.value().clamp(0, 3) as usize],
            "fm1": shared.hardware.load(Ordering::Relaxed),
            "built_in_synth": shared.software.load(Ordering::Relaxed),
        },
        "modes": {
            "kit": params.kit_mode.value(),
            "speech": params.speech_mode.value(),
            "fm1_va": params.firmware_va.value(),
            "control_effects": params.control_fx.value(),
        },
        "voice": params.name.read().map(|n| n.clone()).unwrap_or_default(),
        "kit_tracks": shared.kit().tracks.len(),
        "speech": {
            "state": format!("{:?}", speech.state),
            "summary": speech.summary,
            "pitches_ready": speech.keys_ready,
            "available": shared.speech.available.load(Ordering::Relaxed),
            "speaking": shared.speech.speaking.load(Ordering::Relaxed),
            "text": settings.text,
            "character": settings.character,
            "lint": speech.lint,
        },
    })
}

pub struct Reporter {
    stop: Arc<AtomicBool>,
    thread: Option<JoinHandle<()>>,
}

impl Reporter {
    pub fn start(params: Arc<Fm1Params>) -> Self {
        let stop = Arc::new(AtomicBool::new(false));
        let thread = std::thread::Builder::new()
            .name("fm1-status".into())
            .spawn({
                let stop = stop.clone();
                move || {
                    while !stop.load(Ordering::Relaxed) {
                        if let Some(folder) = folder().filter(|f| f.join("debug").exists()) {
                            let text = serde_json::to_string_pretty(&snapshot(&params)).unwrap_or_default();
                            // Write then rename, so a reader never sees half a file.
                            let partial = folder.join("status.json.part");
                            if std::fs::write(&partial, text).is_ok() {
                                let _ = std::fs::rename(&partial, folder.join("status.json"));
                            }
                        }
                        for _ in 0..10 {
                            if stop.load(Ordering::Relaxed) {
                                return;
                            }
                            std::thread::sleep(Duration::from_millis(100));
                        }
                    }
                }
            })
            .ok();
        Reporter { stop, thread }
    }
}

impl Drop for Reporter {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn snapshot_reports_modes_counters_and_speech_state() {
        let params = Fm1Params::default();
        params.shared.blocks.store(1234, Ordering::Relaxed);
        params.shared.notes.store(7, Ordering::Relaxed);
        let status = snapshot(&params);
        assert_eq!(status["audio"]["blocks"], 1234);
        assert_eq!(status["audio"]["notes_received"], 7);
        assert_eq!(status["midi"]["connected"], false);
        assert_eq!(status["sound"]["mode"], "Auto");
        assert_eq!(status["sound"]["built_in_synth"], false); // set by the audio thread once it runs
        assert_eq!(status["modes"]["speech"], false);
        assert_eq!(status["voice"], "INIT VOICE");
        assert_eq!(status["speech"]["state"], "Idle");
        assert_eq!(status["speech"]["available"], false);
        assert!(status["written_at"].as_u64().unwrap() > 1_700_000_000);
    }
}
