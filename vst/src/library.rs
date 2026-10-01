//! The voice library (`index.json`) and drum kits (`kits.json`) that the workbench builds from
//! the user's own DX7 patch banks, read from disk on a background thread the first time an
//! editor opens. Neither file ships with the project.

use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use serde::Deserialize;

use crate::dx7::{self, Voice};
use crate::kit::{Kit, Macros, Track, BASE_KEY};
use crate::platform;

pub const TAGS: [&str; 16] = [
    "keys", "brass", "bass", "strings", "perc", "pluck", "pad", "lead", "bell", "organ", "wind",
    "fx", "tom", "hat", "snare", "kick",
];
const DRUM_TAGS: [&str; 5] = ["kick", "snare", "hat", "tom", "perc"];

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Filter {
    All,
    Drums,
    Tag(usize),
}

pub struct Entry {
    pub name: [u8; dx7::NAME_LEN],
    lower: [u8; dx7::NAME_LEN],
    pub bank: u16,
    pub slot: Option<u8>,
    tags: u16,
    packed: [u8; 128],
}

impl Entry {
    pub fn name(&self) -> &str {
        std::str::from_utf8(&self.name).unwrap_or("").trim_end()
    }

    pub fn voice(&self) -> Voice {
        dx7::from_packed(&self.packed)
    }

    pub fn first_tag(&self) -> Option<&'static str> {
        (self.tags != 0).then(|| TAGS[self.tags.trailing_zeros() as usize])
    }
}

#[derive(Default)]
pub struct Library {
    pub voices: Vec<Entry>,
    pub banks: Vec<String>,
    banks_lower: Vec<String>,
    pub kits: Vec<Kit>,
    pub source: PathBuf,
}

#[derive(Deserialize)]
struct IndexFile {
    banks: Vec<String>,
    /// `[name, bank index, slot, base64 of the 128-byte packed voice, tags]`
    voices: Vec<(String, usize, Option<u32>, String, Vec<String>)>,
}

#[derive(Deserialize)]
struct KitsFile {
    kits: Vec<KitFile>,
}

#[derive(Deserialize)]
struct KitFile {
    name: String,
    tracks: Vec<TrackFile>,
}

#[derive(Deserialize)]
struct TrackFile {
    name: String,
    note: u8,
    voice: TrackVoice,
    #[serde(default)]
    macros: Macros,
}

#[derive(Deserialize)]
struct TrackVoice {
    b64: String,
}

fn base64(text: &str) -> Option<Vec<u8>> {
    let mut out = Vec::with_capacity(text.len() * 3 / 4);
    let (mut buffer, mut bits) = (0u32, 0u32);
    for c in text.bytes() {
        let value = match c {
            b'A'..=b'Z' => c - b'A',
            b'a'..=b'z' => c - b'a' + 26,
            b'0'..=b'9' => c - b'0' + 52,
            b'+' | b'-' => 62,
            b'/' | b'_' => 63,
            b'=' => break,
            b'\n' | b'\r' | b' ' => continue,
            _ => return None,
        };
        buffer = (buffer << 6) | value as u32;
        bits += 6;
        if bits >= 8 {
            bits -= 8;
            out.push((buffer >> bits) as u8);
        }
    }
    Some(out)
}

fn packed(b64: &str) -> Option<[u8; 128]> {
    base64(b64)?.get(..128)?.try_into().ok()
}

fn name_bytes(name: &str) -> [u8; dx7::NAME_LEN] {
    let mut voice = [0u8; dx7::EDIT_SIZE];
    dx7::set_name(&mut voice, name);
    let mut out = [b' '; dx7::NAME_LEN];
    out.copy_from_slice(&voice[dx7::NAME_START..dx7::NAME_START + dx7::NAME_LEN]);
    out
}

impl Library {
    pub fn parse(index: &str, kits: Option<&str>) -> Result<Library, String> {
        let file: IndexFile = serde_json::from_str(index).map_err(|e| format!("index.json: {e}"))?;
        let mut voices = Vec::with_capacity(file.voices.len());
        for (name, bank, slot, b64, tags) in &file.voices {
            let Some(packed) = packed(b64) else { continue }; // skip a damaged entry, keep the rest
            let name = name_bytes(name);
            let mut mask = 0u16;
            for tag in tags {
                if let Some(bit) = TAGS.iter().position(|t| t == tag) {
                    mask |= 1 << bit;
                }
            }
            voices.push(Entry {
                name,
                lower: name.map(|c| c.to_ascii_lowercase()),
                bank: (*bank).min(file.banks.len().saturating_sub(1)) as u16,
                slot: slot.map(|s| s.min(255) as u8),
                tags: mask,
                packed,
            });
        }
        let kits = match kits {
            None => Vec::new(),
            Some(text) => serde_json::from_str::<KitsFile>(text)
                .map_err(|e| format!("kits.json: {e}"))?
                .kits
                .into_iter()
                .map(|kit| Kit {
                    name: kit.name,
                    tracks: kit
                        .tracks
                        .into_iter()
                        .enumerate()
                        .filter_map(|(i, track)| {
                            let voice = dx7::from_packed(&packed(&track.voice.b64)?);
                            let key = BASE_KEY.saturating_add(i as u8).min(127);
                            let mut out = Track::new(&track.name, key, track.note, &voice);
                            out.macros = track.macros;
                            Some(out)
                        })
                        .collect(),
                })
                .collect(),
        };
        let banks_lower = file.banks.iter().map(|b| b.to_lowercase()).collect();
        Ok(Library { voices, banks: file.banks, banks_lower, kits, source: PathBuf::new() })
    }

    pub fn load(dir: &Path) -> Result<Library, String> {
        let index = std::fs::read_to_string(dir.join("index.json"))
            .map_err(|e| format!("{}: {e}", dir.join("index.json").display()))?;
        let kits = std::fs::read_to_string(dir.join("kits.json")).ok();
        let mut library = Library::parse(&index, kits.as_deref())?;
        library.source = dir.to_path_buf();
        Ok(library)
    }

    /// Last path component of a voice's bank, as shown in the list.
    pub fn bank_name(&self, entry: &Entry) -> &str {
        self.banks.get(entry.bank as usize).map_or("", |b| b.rsplit('/').next().unwrap_or(b))
    }

    /// Indices of voices whose name or bank contains every word of `query` and that pass
    /// `filter`, in library order.
    pub fn search(&self, query: &str, filter: Filter, out: &mut Vec<u32>) {
        out.clear();
        let query = query.to_lowercase();
        let words: Vec<&[u8]> = query.split_whitespace().map(str::as_bytes).collect();
        let drums = DRUM_TAGS
            .iter()
            .filter_map(|t| TAGS.iter().position(|x| x == t))
            .fold(0u16, |mask, bit| mask | 1 << bit);
        let wanted = match filter {
            Filter::All => u16::MAX,
            Filter::Drums => drums,
            Filter::Tag(tag) => 1 << tag.min(15),
        };
        let contains = |hay: &[u8], needle: &[u8]| hay.windows(needle.len()).any(|w| w == needle);
        for (i, entry) in self.voices.iter().enumerate() {
            if filter != Filter::All && entry.tags & wanted == 0 {
                continue;
            }
            let bank = self.banks_lower.get(entry.bank as usize).map_or(&b""[..], |b| b.as_bytes());
            if words.iter().all(|w| contains(&entry.lower, w) || contains(bank, w)) {
                out.push(i as u32);
            }
        }
    }
}

pub enum State {
    Loading,
    Ready(Arc<Library>),
    Failed(String),
}

/// File in the plugin's support folder (`platform::support_dir`) naming the workbench folder,
/// one line. A plugin in a DAW started from the Finder or the Start menu never sees shell
/// environment variables.
pub const PATH_FILE: &str = "workbench-path.txt";

/// Folders that may hold `index.json`, most specific first.
pub fn candidates() -> Vec<PathBuf> {
    let workbench = std::env::var_os("FM1_WORKBENCH").map(PathBuf::from);
    candidates_from(platform::home().as_deref(), platform::support_dir().as_deref(), workbench)
}

fn candidates_from(home: Option<&Path>, support: Option<&Path>, workbench: Option<PathBuf>) -> Vec<PathBuf> {
    let library = |root: PathBuf| root.join("app").join("library");
    let mut dirs: Vec<PathBuf> = workbench.into_iter().map(library).collect();
    if let Some(support) = support {
        if let Ok(text) = std::fs::read_to_string(support.join(PATH_FILE)) {
            let named = text.lines().next().unwrap_or("").trim();
            match (named.strip_prefix("~/").or_else(|| named.strip_prefix("~\\")), home) {
                (Some(rest), Some(home)) => dirs.push(library(home.join(rest))),
                _ if !named.is_empty() => dirs.push(library(PathBuf::from(named))),
                _ => {}
            }
        }
        dirs.push(support.join("library"));
    }
    if let Some(home) = home {
        // A clone, then the folder name of a downloaded zip.
        for name in ["fm1-workbench", "fm1-workbench-main"] {
            dirs.push(library(home.join(name)));
        }
    }
    dirs
}

static STATE: Mutex<Option<Arc<Mutex<State>>>> = Mutex::new(None);

/// The shared library, starting the load on first use. Every plugin instance shares one copy.
pub fn shared() -> Arc<Mutex<State>> {
    let mut slot = STATE.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
    if let Some(state) = slot.as_ref() {
        return state.clone();
    }
    let state = Arc::new(Mutex::new(State::Loading));
    *slot = Some(state.clone());
    let worker = state.clone();
    let _ = std::thread::Builder::new().name("fm1-library".into()).spawn(move || {
        let dirs = candidates();
        let result = match dirs.iter().find(|dir| dir.join("index.json").is_file()) {
            Some(dir) => Library::load(dir).map(Arc::new).map_or_else(State::Failed, State::Ready),
            None => State::Failed(format!(
                "No voice library. Build one from your own .syx banks with the workbench's \
                 build_library.py (see its README), and if the workbench is not in your home \
                 folder as fm1-workbench, put its path in {}. Looked in: {}",
                platform::support_dir().map_or(PATH_FILE.into(), |d| d.join(PATH_FILE).display().to_string()),
                dirs.iter().map(|d| d.display().to_string()).collect::<Vec<_>>().join(", ")
            )),
        };
        *worker.lock().unwrap_or_else(|poisoned| poisoned.into_inner()) = result;
    });
    state
}

#[cfg(test)]
mod tests {
    use super::*;

    const B64: &str = "MWNjY2NjYwAnAAAAOAAAAgBjY2NjY2NjACcAAAA4DAACAGNjY2NjY2MAJwAAAEgAAAIAY2NjY2NjYwAnAAAAOABGBgBjY2NjY2NjACcAAAA4AFACAGNjY2NjY2MAJwAAADgAYwIAY2NjYzIyMjIVDygAAAAxGFRFU1QgTEVBRCA=";

    fn sample() -> Library {
        let index = format!(
            r#"{{"source": "x", "banks": ["My Banks/Factory/BANK1", "Other/Drums"],
                "voices": [["TEST LEAD ", 0, 0, "{B64}", ["lead"]],
                           ["Kick Hard", 1, 5, "{B64}", ["kick", "perc"]],
                           ["broken", 1, null, "!!!", []],
                           ["E.PIANO 1", 0, null, "{B64}", []]]}}"#
        );
        let kits = format!(
            r#"{{"measured": true, "kits": [{{"name": "K", "source": "s", "tracks": [
                {{"name": "Kick", "role": "kick", "gate": 120, "note": 36,
                  "voice": {{"name": "n", "lib": 1, "b64": "{B64}"}}, "macros": {{"level": -16}}}},
                {{"name": "Hat", "role": "hat", "gate": 50, "note": 54,
                  "voice": {{"name": "n", "b64": "{B64}"}}, "macros": {{}}}}]}}]}}"#
        );
        Library::parse(&index, Some(&kits)).unwrap()
    }

    #[test]
    fn packed_voice_decodes_with_its_name_tags_and_bank() {
        let library = sample();
        assert_eq!(library.voices.len(), 3); // the damaged entry is skipped
        let lead = &library.voices[0];
        assert_eq!((lead.name(), lead.first_tag(), lead.slot), ("TEST LEAD", Some("lead"), Some(0)));
        assert_eq!(library.bank_name(lead), "BANK1");
        let voice = lead.voice();
        // The test voice (made for these tests): algorithm 22, feedback 7, operator 6 rate 1 = 49.
        assert_eq!((voice[dx7::global::ALG], voice[dx7::global::FB], voice[0]), (21, 7, 49));
        assert_eq!(dx7::name_of(&voice), "TEST LEAD");
        assert_eq!(voice[dx7::OP_MASK], 63);
    }

    #[test]
    fn search_matches_name_or_bank_words_and_tag_filters() {
        let library = sample();
        let mut found = Vec::new();
        let mut run = |query: &str, filter| {
            library.search(query, filter, &mut found);
            found.clone()
        };
        assert_eq!(run("", Filter::All), vec![0, 1, 2]);
        assert_eq!(run("piano", Filter::All), vec![2]);
        assert_eq!(run("BANK1", Filter::All), vec![0, 2]); // bank name, any case
        assert_eq!(run("bank1 lead", Filter::All), vec![0]); // every word must match
        assert_eq!(run("", Filter::Drums), vec![1]);
        assert_eq!(run("", Filter::Tag(7)), vec![0]);
        assert_eq!(run("hard", Filter::Tag(7)), Vec::<u32>::new());
    }

    #[test]
    fn kits_map_tracks_to_consecutive_keys_with_their_macros() {
        let kit = &sample().kits[0];
        assert_eq!(kit.name, "K");
        let keys: Vec<_> = kit.tracks.iter().map(|t| (t.name.as_str(), t.key, t.note)).collect();
        assert_eq!(keys, vec![("Kick", 36, 36), ("Hat", 37, 54)]);
        assert_eq!(kit.tracks[0].macros.level, -16);
        assert_eq!(kit.tracks[1].voice_name(), "TEST LEAD");
    }

    /// A library built on this machine from the user's own banks, when there is one.
    #[test]
    fn locally_built_library_loads_completely() {
        let Some(dir) = candidates().into_iter().find(|d| d.join("index.json").is_file()) else {
            eprintln!("no locally built library; skipped");
            return;
        };
        let library = Library::load(&dir).unwrap_or_else(|e| panic!("{e}"));
        assert!(!library.voices.is_empty());
        for entry in &library.voices {
            let voice = entry.voice();
            assert!((0..dx7::VOICE_PARAMS).all(|i| voice[i] <= dx7::field_of(i).max));
        }
        let mut found = Vec::new();
        library.search("", Filter::All, &mut found);
        assert_eq!(found.len(), library.voices.len());
        assert!(library.kits.iter().all(|k| !k.tracks.is_empty()));
    }

    #[test]
    fn workbench_folder_is_found_by_environment_path_file_or_home() {
        let home = std::env::temp_dir().join(format!("fm1-home-{}", std::process::id()));
        let support = home.join("support").join("FM-1 Controller");
        std::fs::create_dir_all(&support).unwrap();
        let plain = candidates_from(Some(&home), Some(&support), None);
        assert_eq!(plain, vec![
            support.join("library"),
            home.join("fm1-workbench").join("app").join("library"),
            home.join("fm1-workbench-main").join("app").join("library"),
        ]);
        std::fs::write(support.join(PATH_FILE), "~/Music/tools/workbench \n# anything after the first line is ignored\n").unwrap();
        let named = candidates_from(Some(&home), Some(&support), Some(PathBuf::from("/opt/wb")));
        assert_eq!(named[0], PathBuf::from("/opt/wb").join("app").join("library")); // the environment wins
        assert_eq!(named[1], home.join("Music/tools/workbench").join("app").join("library"));
        assert_eq!(named.len(), 5);
        std::fs::write(support.join(PATH_FILE), "/abs/wb\n").unwrap();
        let absolute = candidates_from(Some(&home), Some(&support), None);
        assert_eq!(absolute[0], PathBuf::from("/abs/wb").join("app").join("library"));
        assert!(candidates_from(None, None, None).is_empty());
        let _ = std::fs::remove_dir_all(&home);
    }

    #[test]
    fn missing_or_malformed_files_report_rather_than_panic() {
        assert!(Library::parse("not json", None).is_err());
        assert!(Library::parse(r#"{"banks": [], "voices": []}"#, Some("{}")).is_err());
        let missing = Library::load(Path::new("/nonexistent")).err().unwrap_or_default();
        assert!(missing.contains("index.json"));
        assert_eq!(base64("TQ==").unwrap(), b"M");
        assert_eq!(base64("TWFu").unwrap(), b"Man");
        assert!(base64("M*").is_none());
    }
}
