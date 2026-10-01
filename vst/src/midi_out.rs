//! Direct MIDI output to the FM-1, bypassing the host's MIDI routing.
//!
//! The audio thread pushes timestamped messages into a lock-free ring; a sender thread owns the
//! MIDI port and forwards them. Messages carry the time at which they are due.
//!
//! - macOS: CoreMIDI takes the timestamp and schedules the message itself, so the sender
//!   thread's wake-up jitter does not reach the notes.
//! - Windows: WinMM (through `midir`) has no timestamps, so the sender thread holds each message
//!   until it is due and sends it then; timing is as good as that thread's wake-ups, about a
//!   millisecond. A WinMM output port can be open in only one program: the FM-1's output must be
//!   switched off in the host's own MIDI settings or the plugin cannot open it.
//! - Elsewhere there is no output yet; the plugin loads and its editor works.

use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::Arc;
use std::thread::JoinHandle;
use std::time::Duration;

use crate::engine::Msg;

const QUEUE: usize = 4096;
/// Device name fragment; the port has been seen as "FM-1 Audio 1" and "FM-1 Midi 1".
pub const DEVICE: &str = "FM-1";

#[derive(Clone, Copy)]
struct Timed {
    /// Host time (mach ticks) at which the message is due; 0 means now.
    when: u64,
    msg: Msg,
}

/// State the sender thread publishes for the audio thread and the editor.
#[derive(Default)]
pub struct Link {
    pub connected: AtomicBool,
    /// Bumped each time the FM-1 appears, so the engine knows to resend the whole voice.
    pub generation: AtomicU32,
    pub sent: AtomicU32,
    pub dropped: AtomicU32,
}

pub struct MidiOut {
    queue: rtrb::Producer<Timed>,
    pub link: Arc<Link>,
    stop: Arc<AtomicBool>,
    thread: Option<JoinHandle<()>>,
}

impl MidiOut {
    pub fn start(link: Arc<Link>) -> Self {
        let (queue, consumer) = rtrb::RingBuffer::new(QUEUE);
        let stop = Arc::new(AtomicBool::new(false));
        let thread = std::thread::Builder::new()
            .name("fm1-midi-out".into())
            .spawn({
                let (link, stop) = (link.clone(), stop.clone());
                move || sender(consumer, link, stop)
            })
            .ok();
        MidiOut { queue, link, stop, thread }
    }

    /// Queue a message for `when` (host ticks, 0 = now). Returns false if the queue is full.
    /// Realtime-safe: no allocation, no lock.
    pub fn push(&mut self, when: u64, msg: Msg) -> bool {
        self.queue.push(Timed { when, msg }).is_ok()
    }

    pub fn full(&self) -> bool {
        self.queue.is_full()
    }

    /// An output with no sender thread and no device: the test reads what would have been sent.
    #[cfg(test)]
    pub fn detached(link: Arc<Link>) -> (Self, Sent) {
        let (queue, consumer) = rtrb::RingBuffer::new(QUEUE);
        let out = MidiOut { queue, link, stop: Arc::new(AtomicBool::new(false)), thread: None };
        (out, Sent(consumer))
    }
}

#[cfg(test)]
pub struct Sent(rtrb::Consumer<Timed>);

#[cfg(test)]
impl Sent {
    /// Everything queued since the last call, with the host time each message is due.
    pub fn take(&mut self) -> Vec<(u64, Msg)> {
        std::iter::from_fn(|| self.0.pop().ok()).map(|timed| (timed.when, timed.msg)).collect()
    }
}

impl Drop for MidiOut {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

#[cfg(target_os = "macos")]
pub mod clock {
    use mach2::mach_time::{mach_absolute_time, mach_timebase_info};

    /// Host ticks per second.
    pub fn rate() -> f64 {
        let mut info = mach_timebase_info { numer: 0, denom: 0 };
        // SAFETY: mach_timebase_info only writes the struct it is given.
        unsafe { mach_timebase_info(&mut info) };
        if info.numer == 0 {
            return 1e9;
        }
        1e9 * info.denom as f64 / info.numer as f64
    }

    pub fn now() -> u64 {
        // SAFETY: no arguments, no side effects.
        unsafe { mach_absolute_time() }
    }
}

#[cfg(not(target_os = "macos"))]
pub mod clock {
    use std::sync::OnceLock;
    use std::time::Instant;

    static START: OnceLock<Instant> = OnceLock::new();

    /// Ticks per second: nanoseconds.
    pub fn rate() -> f64 {
        1e9
    }

    /// Nanoseconds since the first call. Never 0, which means "now" to the sender.
    pub fn now() -> u64 {
        START.get_or_init(Instant::now).elapsed().as_nanos() as u64 + 1
    }
}

#[cfg(target_os = "macos")]
fn sender(mut queue: rtrb::Consumer<Timed>, link: Arc<Link>, stop: Arc<AtomicBool>) {
    use coremidi::{Client, Destination, Destinations, PacketBuffer};

    // FM1_MIDI_OUT names another output (a fragment of its name), for tests and unusual setups.
    let wanted = std::env::var("FM1_MIDI_OUT").unwrap_or_else(|_| DEVICE.to_string());
    let find = || -> Option<Destination> {
        Destinations.into_iter().find(|d| d.display_name().is_some_and(|n| n.contains(&wanted)))
    };
    let port = Client::new("FM-1 Controller").and_then(|client| {
        let port = client.output_port("FM-1 Controller out")?;
        Ok((client, port))
    });
    let Ok((_client, port)) = port else {
        // CoreMIDI is unavailable: keep draining so the audio thread's queue never fills.
        while !stop.load(Ordering::Relaxed) {
            while let Ok(_) = queue.pop() {
                link.dropped.fetch_add(1, Ordering::Relaxed);
            }
            std::thread::sleep(Duration::from_millis(20));
        }
        return;
    };

    let mut destination: Option<Destination> = None;
    let mut idle_polls = 0u32;
    loop {
        // Read the flag before draining, so that whatever was queued before shutdown (the
        // note-offs that release held notes) is still sent on the final pass.
        let stopping = stop.load(Ordering::Relaxed);
        // Re-resolve the endpoint twice a second: the FM-1 sleeps and re-enumerates.
        if idle_polls == 0 {
            let found = find();
            let was = link.connected.swap(found.is_some(), Ordering::Relaxed);
            if found.is_some() && !was {
                link.generation.fetch_add(1, Ordering::Relaxed);
            }
            destination = found;
        }
        idle_polls = (idle_polls + 1) % 1000;

        while let Ok(Timed { when, msg }) = queue.pop() {
            // No pacing: a whole voice sent in one burst, with a note straight after it, arrives
            // intact (checked against voices loaded slowly; see the README).
            let delivered = destination
                .as_ref()
                .is_some_and(|d| port.send(d, &PacketBuffer::new(when, msg.data())).is_ok());
            if delivered {
                link.sent.fetch_add(1, Ordering::Relaxed);
            } else {
                link.dropped.fetch_add(1, Ordering::Relaxed);
                idle_polls = 0; // look for the device again on the next pass
            }
        }
        if stopping {
            break;
        }
        std::thread::sleep(Duration::from_micros(500));
    }
}

#[cfg(target_os = "windows")]
fn sender(mut queue: rtrb::Consumer<Timed>, link: Arc<Link>, stop: Arc<AtomicBool>) {
    use std::cmp::Reverse;
    use std::collections::BinaryHeap;

    use midir::{MidiOutput, MidiOutputConnection};

    // FM1_MIDI_OUT names another output (a fragment of its name), for tests and unusual setups.
    let wanted = std::env::var("FM1_MIDI_OUT").unwrap_or_else(|_| DEVICE.to_string());
    let connect = || -> Option<MidiOutputConnection> {
        let output = MidiOutput::new("FM-1 Controller").ok()?;
        let port = output
            .ports()
            .into_iter()
            .find(|port| output.port_name(port).is_ok_and(|name| name.contains(&wanted)))?;
        output.connect(&port, "FM-1 Controller out").ok()
    };

    let mut connection: Option<MidiOutputConnection> = None;
    // Waiting messages, soonest first; the counter keeps messages with equal times in order.
    let mut waiting: BinaryHeap<Reverse<(u64, u64, u8, [u8; 7])>> = BinaryHeap::new();
    let mut counter = 0u64;
    let mut idle_polls = 0u32;
    loop {
        // Read the flag before draining, so that whatever was queued before shutdown (the
        // note-offs that release held notes) is still sent on the final pass.
        let stopping = stop.load(Ordering::Relaxed);
        // Look for the port twice a second while there is none: the FM-1 sleeps and re-enumerates.
        if connection.is_none() && idle_polls == 0 {
            connection = connect();
            let was = link.connected.swap(connection.is_some(), Ordering::Relaxed);
            if connection.is_some() && !was {
                link.generation.fetch_add(1, Ordering::Relaxed);
            }
        }
        idle_polls = (idle_polls + 1) % 1000;

        while let Ok(Timed { when, msg }) = queue.pop() {
            waiting.push(Reverse((when, counter, msg.len, msg.bytes)));
            counter += 1;
        }
        let now = clock::now();
        while let Some(Reverse((when, _, len, bytes))) = waiting.peek().copied() {
            if when > now && !stopping {
                break;
            }
            waiting.pop();
            let delivered =
                connection.as_mut().is_some_and(|port| port.send(&bytes[..len as usize]).is_ok());
            if delivered {
                link.sent.fetch_add(1, Ordering::Relaxed);
            } else {
                link.dropped.fetch_add(1, Ordering::Relaxed);
                if connection.take().is_some() {
                    link.connected.store(false, Ordering::Relaxed);
                    idle_polls = 0; // the port went away: look for it again on the next pass
                }
            }
        }
        if stopping {
            break;
        }
        std::thread::sleep(Duration::from_micros(500));
    }
}

#[cfg(not(any(target_os = "macos", target_os = "windows")))]
fn sender(mut queue: rtrb::Consumer<Timed>, link: Arc<Link>, stop: Arc<AtomicBool>) {
    // No MIDI output on this platform yet. Drain the queue so the plugin still loads.
    while !stop.load(Ordering::Relaxed) {
        while queue.pop().is_ok() {
            link.dropped.fetch_add(1, Ordering::Relaxed);
        }
        std::thread::sleep(Duration::from_millis(20));
    }
}
