//! The few things that differ between operating systems, other than MIDI output (`midi_out`).

use std::path::PathBuf;

/// The user's home folder.
pub fn home() -> Option<PathBuf> {
    let variable = if cfg!(target_os = "windows") { "USERPROFILE" } else { "HOME" };
    std::env::var_os(variable).map(PathBuf::from)
}

/// The plugin's own folder for its path file, an installed library and the status file:
/// `~/Library/Application Support/FM-1 Controller` on macOS, `%APPDATA%\FM-1 Controller` on
/// Windows, `~/.config/FM-1 Controller` elsewhere.
pub fn support_dir() -> Option<PathBuf> {
    let base = if cfg!(target_os = "macos") {
        home()?.join("Library").join("Application Support")
    } else if cfg!(target_os = "windows") {
        PathBuf::from(std::env::var_os("APPDATA")?)
    } else {
        home()?.join(".config")
    };
    Some(base.join("FM-1 Controller"))
}

/// The system clipboard's text, through the platform's own tool, so that pasting works even
/// in a host that keeps keyboard shortcuts for itself.
pub fn clipboard_text() -> Option<String> {
    let mut command = if cfg!(target_os = "macos") {
        std::process::Command::new("pbpaste")
    } else if cfg!(target_os = "windows") {
        let mut powershell = std::process::Command::new("powershell");
        powershell.args(["-NoProfile", "-NonInteractive", "-Command", "Get-Clipboard -Raw"]);
        #[cfg(target_os = "windows")]
        {
            use std::os::windows::process::CommandExt;
            powershell.creation_flags(0x0800_0000); // CREATE_NO_WINDOW: no console flashing up
        }
        powershell
    } else {
        let mut xclip = std::process::Command::new("xclip");
        xclip.args(["-selection", "clipboard", "-o"]);
        xclip
    };
    let output = command.output().ok()?;
    let text = String::from_utf8_lossy(&output.stdout).trim().to_string();
    (output.status.success() && !text.is_empty()).then_some(text)
}
