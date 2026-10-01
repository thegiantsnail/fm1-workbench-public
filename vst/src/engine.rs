//! What to send to the FM-1, given what it should be doing. No I/O and no allocation here: the
//! audio thread calls this and hands the resulting messages to the MIDI sender.
//!
//! The rules come from measurements on the hardware (the workbench's FINDINGS.md):
//! - Voices change through single-parameter SysEx; only parameters that differ are sent.
//! - The device transpose never changes. A note-off is matched using the transpose in effect at
//!   note-off time, so changing it under a held note hangs that note. Notes are shifted instead.
//! - A note ends with a real note-off (0x80). Note-on with velocity 0 keeps sounding.
//! - CC 120/123 are ignored, so panic sends a note-off for every key.
//! - Full voice dumps are never sent: stock firmware stalls on them, and Baud Girl's FM-1+VA
//!   firmware overwrites the selected stored preset with one, without asking.
//! - On FM-1+VA the voice's LFO speed and delay only reach the running LFO through CC 76 and 78,
//!   and CC 7 is a master volume. Stock firmware ignores all three, so they are sent only when
//!   the plugin is told the unit runs FM-1+VA.

use crate::dx7::{self, EDIT_SIZE, NAME_START, OP_MASK, TRANSPOSE, TRANSPOSE_NEUTRAL};

pub const FX_COUNT: usize = 24;
const UNKNOWN: i16 = -1;
const NOT_HELD: i16 = -1;
const SUSTAIN_CC: u8 = 64;
const VOLUME_CC: u8 = 7;
/// (voice parameter, controller) for the FM-1+VA firmware's LFO speed and delay.
const LFO_CCS: [(usize, u8); 2] = [(137, 76), (138, 78)];

/// One MIDI message, timed in samples from the start of the current block.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Msg {
    pub offset: u32,
    pub len: u8,
    pub bytes: [u8; 7],
}

impl Msg {
    fn short(offset: u32, status: u8, a: u8, b: u8) -> Self {
        Msg { offset, len: 3, bytes: [status, a & 0x7F, b & 0x7F, 0, 0, 0, 0] }
    }

    pub fn data(&self) -> &[u8] {
        &self.bytes[..self.len as usize]
    }
}

/// When a kit hit's voice change and its note go out, in samples from the block start.
///
/// The FM-1 needs up to about 14 ms to take in the parameter differences between two voices, and
/// a note sent straight after them waits behind them. So the change is sent when the hit arrives
/// and the note `lead` later; every hit is late by the same `lead`, which the host can compensate.
/// The change may not start before the previous hit's note-on (`last_note`), or that note would
/// sound with the wrong voice.
pub fn kit_schedule(hit: i64, lead: i64, last_note: i64) -> (i64, i64) {
    let change = hit.max(last_note + 1);
    (change, (hit + lead).max(change))
}

pub struct Engine {
    /// What the FM-1's edit buffer holds, as far as we have told it. `UNKNOWN` until sent.
    voice: [i16; EDIT_SIZE],
    fx: [i16; FX_COUNT],
    /// FM-1+VA only: the LFO controller values and master volume last sent.
    lfo: [i16; 2],
    volume: i16,
    /// The note number actually sent for each incoming note, so its note-off matches even if the
    /// transpose parameter moved in between.
    held: [i16; 128],
}

impl Default for Engine {
    fn default() -> Self {
        Engine {
            voice: [UNKNOWN; EDIT_SIZE],
            fx: [UNKNOWN; FX_COUNT],
            lfo: [UNKNOWN; 2],
            volume: UNKNOWN,
            held: [NOT_HELD; 128],
        }
    }
}

impl Engine {
    /// The device may hold anything (first use, reconnect, edited on its own knobs): the next
    /// sync resends everything.
    pub fn forget(&mut self) {
        self.voice = [UNKNOWN; EDIT_SIZE];
        self.fx = [UNKNOWN; FX_COUNT];
        self.lfo = [UNKNOWN; 2];
        self.volume = UNKNOWN;
    }

    pub fn forget_fx(&mut self) {
        self.fx = [UNKNOWN; FX_COUNT];
    }

    /// Note shift that stands in for the voice's transpose.
    pub fn shift(want: &[u8; EDIT_SIZE]) -> i32 {
        want[TRANSPOSE] as i32 - TRANSPOSE_NEUTRAL as i32
    }

    /// Send the voice parameters that differ from what the device holds. `send` returns false
    /// when its queue is full; that parameter stays marked as unsent and is retried next block.
    /// Returns the number of messages sent. `offset` times them within the block: a kit sends a
    /// voice's differences at the moment of the note that needs them.
    pub fn sync_voice(
        &mut self,
        want: &[u8; EDIT_SIZE],
        channel: u8,
        offset: u32,
        send: &mut impl FnMut(Msg) -> bool,
    ) -> usize {
        let mut sent = 0;
        // Voice parameters, then the operator mask, then the name, as the workbench sends them.
        let order = (0..NAME_START).chain([OP_MASK]).chain(NAME_START..OP_MASK);
        for index in order {
            let value = match index {
                TRANSPOSE => TRANSPOSE_NEUTRAL,
                OP_MASK => 63,
                _ => want[index].min(127),
            };
            if self.voice[index] == value as i16 {
                continue;
            }
            let bytes = dx7::param_change(channel, index, value);
            if !send(Msg { offset, len: 7, bytes }) {
                break;
            }
            self.voice[index] = value as i16;
            sent += 1;
        }
        sent
    }

    /// FM-1+VA: send the voice's LFO speed and delay as CC 76 and 78 on the note channel, scaled
    /// from 0..99 to 0..127 as the workbench does, when they differ from what was last sent.
    ///
    /// Each is sent twice, a neighbouring value first. Measured on FM-1+VA 093: once the voice's
    /// LFO speed parameter has arrived by SysEx, a CC 76 carrying that same speed is ignored and
    /// the LFO keeps its old rate (speed 31 then CC 40: no change; CC 41, or 39 then 40: applied).
    /// The voice parameters always go out before this, so the plain value would be the ignored one.
    pub fn sync_lfo(
        &mut self,
        want: &[u8; EDIT_SIZE],
        channel: u8,
        offset: u32,
        send: &mut impl FnMut(Msg) -> bool,
    ) -> usize {
        let mut sent = 0;
        for (slot, (param, cc)) in LFO_CCS.into_iter().enumerate() {
            let value = (want[param].min(99) as f32 * 127.0 / 99.0).round() as u8;
            let status = 0xB0 | (channel & 0x0F);
            if self.lfo[slot] != value as i16
                && send(Msg::short(offset, status, cc, value ^ 1))
                && send(Msg::short(offset, status, cc, value))
            {
                self.lfo[slot] = value as i16;
                sent += 1;
            }
        }
        sent
    }

    /// FM-1+VA: master volume (CC 7 on the note channel). The caller must not use this when the
    /// note and effect channels are the same: there CC 7 is the reverb mix.
    pub fn sync_volume(&mut self, volume: u8, channel: u8, send: &mut impl FnMut(Msg) -> bool) {
        let volume = volume.min(127);
        if self.volume != volume as i16
            && send(Msg::short(0, 0xB0 | (channel & 0x0F), VOLUME_CC, volume))
        {
            self.volume = volume as i16;
        }
    }

    /// Send the effect controllers (CC 0..23 on the effect channel) that differ.
    pub fn sync_fx(
        &mut self,
        want: &[u8; FX_COUNT],
        channel: u8,
        send: &mut impl FnMut(Msg) -> bool,
    ) -> usize {
        let mut sent = 0;
        for (cc, &value) in want.iter().enumerate() {
            if self.fx[cc] == value as i16 {
                continue;
            }
            if !send(Msg::short(0, 0xB0 | (channel & 0x0F), cc as u8, value)) {
                break;
            }
            self.fx[cc] = value as i16;
            sent += 1;
        }
        sent
    }

    pub fn note_on(
        &mut self,
        note: u8,
        velocity: u8,
        shift: i32,
        channel: u8,
        offset: u32,
        send: &mut impl FnMut(Msg) -> bool,
    ) {
        self.note_on_as(note, note as i32 + shift, velocity, channel, offset, send);
    }

    /// Start `pitch` on the device for incoming `key`. A kit plays each key at its track's own
    /// pitch; the later note-off for `key` releases whatever pitch was started here.
    pub fn note_on_as(
        &mut self,
        key: u8,
        pitch: i32,
        velocity: u8,
        channel: u8,
        offset: u32,
        send: &mut impl FnMut(Msg) -> bool,
    ) {
        let key = (key & 0x7F) as usize;
        if self.held[key] != NOT_HELD {
            // Retrigger: end the earlier note first or its voice is never released.
            self.note_off(key as u8, channel, offset, send);
        }
        let pitch = pitch.clamp(0, 127) as u8;
        if send(Msg::short(offset, 0x90 | (channel & 0x0F), pitch, velocity.clamp(1, 127))) {
            self.held[key] = pitch as i16;
        }
    }

    pub fn note_off(
        &mut self,
        note: u8,
        channel: u8,
        offset: u32,
        send: &mut impl FnMut(Msg) -> bool,
    ) {
        let note = (note & 0x7F) as usize;
        let pitch = self.held[note];
        if pitch == NOT_HELD {
            return;
        }
        if send(Msg::short(offset, 0x80 | (channel & 0x0F), pitch as u8, 0)) {
            self.held[note] = NOT_HELD;
        }
    }

    pub fn sustain(&self, on: bool, channel: u8, offset: u32, send: &mut impl FnMut(Msg) -> bool) {
        send(Msg::short(offset, 0xB0 | (channel & 0x0F), SUSTAIN_CC, if on { 127 } else { 0 }));
    }

    /// Release the notes this plugin started, and the sustain pedal.
    pub fn release_all(&mut self, channel: u8, send: &mut impl FnMut(Msg) -> bool) {
        for note in 0..128u8 {
            self.note_off(note, channel, 0, send);
        }
        self.sustain(false, channel, 0, send);
    }

    /// Note-off for every key, whoever started it.
    pub fn panic(&mut self, channel: u8, send: &mut impl FnMut(Msg) -> bool) {
        for pitch in 0..128u8 {
            send(Msg::short(0, 0x80 | (channel & 0x0F), pitch, 0));
        }
        self.held = [NOT_HELD; 128];
        self.sustain(false, channel, 0, send);
    }

    pub fn held_count(&self) -> usize {
        self.held.iter().filter(|&&p| p != NOT_HELD).count()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn collect(run: impl FnOnce(&mut dyn FnMut(Msg) -> bool)) -> Vec<Msg> {
        let mut out = Vec::new();
        run(&mut |msg| {
            out.push(msg);
            true
        });
        out
    }

    #[test]
    fn first_sync_sends_the_whole_voice_then_only_differences() {
        let mut engine = Engine::default();
        let mut voice = dx7::init_voice();
        let first = collect(|send| {
            engine.sync_voice(&voice, 0, 0, &mut |m| send(m));
        });
        assert_eq!(first.len(), EDIT_SIZE);
        assert_eq!(first[0].data(), &[0xF0, 0x43, 0x10, 0, 0, 99, 0xF7]);
        assert_eq!(first[145].bytes[3..5], [1, 27]); // operator mask goes before the name

        assert!(collect(|send| {
            engine.sync_voice(&voice, 0, 0, &mut |m| send(m));
        })
        .is_empty());

        voice[dx7::global_index(8)] = 4; // algorithm 5
        voice[dx7::op_index(2, 16)] = 80;
        let diff = collect(|send| {
            engine.sync_voice(&voice, 0, 0, &mut |m| send(m));
        });
        let changed: Vec<_> = diff.iter().map(|m| (m.bytes[4], m.bytes[5])).collect();
        assert_eq!(changed, vec![(100, 80), (134 - 128, 4)]);
        assert_eq!(diff[1].bytes[3], 1);
    }

    #[test]
    fn transpose_never_reaches_the_device_and_becomes_a_note_shift() {
        let mut engine = Engine::default();
        let mut voice = dx7::init_voice();
        voice[TRANSPOSE] = 12; // an octave down, as half the library uses
        let sent = collect(|send| {
            engine.sync_voice(&voice, 0, 0, &mut |m| send(m));
        });
        let transpose = sent.iter().find(|m| m.bytes[3] == 1 && m.bytes[4] == 16).unwrap();
        assert_eq!(transpose.bytes[5], TRANSPOSE_NEUTRAL);
        assert_eq!(Engine::shift(&voice), -12);

        let notes = collect(|send| {
            engine.note_on(60, 100, Engine::shift(&voice), 0, 7, &mut |m| send(m));
            // The transpose parameter moves while the note is held...
            engine.note_off(60, 0, 90, &mut |m| send(m));
        });
        // ...and the note-off still names the pitch that was started, as a real note-off.
        assert_eq!(notes[0], Msg { offset: 7, len: 3, bytes: [0x90, 48, 100, 0, 0, 0, 0] });
        assert_eq!(notes[1], Msg { offset: 90, len: 3, bytes: [0x80, 48, 0, 0, 0, 0, 0] });
        assert_eq!(engine.held_count(), 0);
    }

    #[test]
    fn retrigger_and_stray_note_off_never_leave_a_voice_hanging() {
        let mut engine = Engine::default();
        let sent = collect(|send| {
            engine.note_off(64, 0, 0, &mut |m| send(m)); // never started: nothing to send
            engine.note_on(64, 0, 0, 0, 0, &mut |m| send(m)); // velocity 0 would not sound
            engine.note_on(64, 90, 5, 0, 10, &mut |m| send(m)); // retrigger with a new shift
        });
        let status: Vec<_> = sent.iter().map(|m| (m.bytes[0], m.bytes[1], m.bytes[2])).collect();
        assert_eq!(status, vec![(0x90, 64, 1), (0x80, 64, 0), (0x90, 69, 90)]);
        assert_eq!(engine.held_count(), 1);
    }

    #[test]
    fn full_queue_leaves_parameters_marked_unsent() {
        let mut engine = Engine::default();
        let voice = dx7::init_voice();
        let mut room = 100;
        let mut first = 0;
        engine.sync_voice(&voice, 0, 0, &mut |_| {
            first += 1;
            room -= 1;
            room >= 0
        });
        assert_eq!(first, 101); // the 101st was refused
        let rest = collect(|send| {
            engine.sync_voice(&voice, 0, 0, &mut |m| send(m));
        });
        assert_eq!(rest.len(), EDIT_SIZE - 100);

        let mut dropped = Engine::default();
        dropped.note_on(60, 90, 0, 0, 0, &mut |_| false);
        assert_eq!(dropped.held_count(), 0); // a note that never left is not tracked
    }

    #[test]
    fn effects_use_their_own_channel_and_forget_resends_everything() {
        let mut engine = Engine::default();
        let mut fx = [0u8; FX_COUNT];
        fx[2] = 107;
        let first = collect(|send| {
            engine.sync_fx(&fx, 1, &mut |m| send(m));
        });
        assert_eq!(first.len(), FX_COUNT);
        assert_eq!(first[2].data(), &[0xB1, 2, 107]);
        fx[7] = 30;
        let diff = collect(|send| {
            engine.sync_fx(&fx, 1, &mut |m| send(m));
        });
        assert_eq!(diff.len(), 1);
        assert_eq!(diff[0].data(), &[0xB1, 7, 30]);
        engine.forget();
        assert_eq!(
            collect(|send| {
                engine.sync_fx(&fx, 1, &mut |m| send(m));
            })
            .len(),
            FX_COUNT
        );
    }

    #[test]
    fn kit_hit_switches_voice_at_the_note_and_releases_the_pitch_it_started() {
        let mut engine = Engine::default();
        let (kick, mut hat) = (dx7::init_voice(), dx7::init_voice());
        hat[dx7::op_index(2, 16)] = 70;
        hat[dx7::global_index(8)] = 31;
        collect(|send| {
            engine.sync_voice(&kick, 0, 0, &mut |m| send(m));
        });
        let hit = collect(|send| {
            engine.sync_voice(&hat, 0, 480, &mut |m| send(m));
            engine.note_on_as(37, 54, 100, 0, 480, &mut |m| send(m));
            engine.note_off(37, 0, 900, &mut |m| send(m));
        });
        // Two differing parameters, timed with the note, then the track's pitch rather than the key.
        assert_eq!(hit.len(), 4);
        assert!(hit[..3].iter().all(|m| m.offset == 480));
        assert_eq!(hit[2].data(), &[0x90, 54, 100]);
        assert_eq!(hit[3].data(), &[0x80, 54, 0]);
    }

    #[test]
    fn kit_hits_change_voice_early_but_never_under_the_previous_note() {
        // A lone hit at sample 100 with 882 samples (20 ms) of lead.
        assert_eq!(kit_schedule(100, 882, -5000), (100, 982));
        // The previous hit's note is still to come at 700: the change waits for it.
        assert_eq!(kit_schedule(300, 882, 700), (701, 1182));
        // Hits closer together than the lead: the note cannot precede its own voice change.
        assert_eq!(kit_schedule(300, 100, 700), (701, 701));
        // No lead: change and note together, as before.
        assert_eq!(kit_schedule(64, 0, -1), (64, 64));
    }

    #[test]
    fn va_firmware_gets_lfo_and_volume_as_controllers_only_when_they_change() {
        let mut engine = Engine::default();
        let mut voice = dx7::init_voice();
        voice[137] = 31; // LFO speed: the workbench measured CC 40 = 5.0 Hz on FM-1+VA
        voice[138] = 99;
        let first = collect(|send| {
            engine.sync_lfo(&voice, 0, 9, &mut |m| send(m));
            engine.sync_volume(100, 0, &mut |m| send(m));
        });
        // Each LFO controller goes out as a neighbouring value, then the value itself.
        assert_eq!(first[0], Msg { offset: 9, len: 3, bytes: [0xB0, 76, 41, 0, 0, 0, 0] });
        assert_eq!(first[1].data(), &[0xB0, 76, 40]);
        assert_eq!(first[2].data(), &[0xB0, 78, 126]);
        assert_eq!(first[3].data(), &[0xB0, 78, 127]);
        assert_eq!(first[4].data(), &[0xB0, 7, 100]);
        let again = collect(|send| {
            engine.sync_lfo(&voice, 0, 0, &mut |m| send(m));
            engine.sync_volume(100, 0, &mut |m| send(m));
        });
        assert!(again.is_empty());
        voice[137] = 62;
        let changed = collect(|send| {
            engine.sync_lfo(&voice, 3, 0, &mut |m| send(m));
            engine.sync_volume(200, 3, &mut |m| send(m));
        });
        assert_eq!(changed[0].data(), &[0xB3, 76, 81]);
        assert_eq!(changed[1].data(), &[0xB3, 76, 80]); // 62 * 127 / 99 = 79.5, rounds up
        assert_eq!(changed[2].data(), &[0xB3, 7, 127]);
        engine.forget(); // reconnect: the unit's LFO and volume are unknown again
        let resent = collect(|send| {
            engine.sync_lfo(&voice, 0, 0, &mut |m| send(m));
            engine.sync_volume(127, 0, &mut |m| send(m));
        });
        assert_eq!(resent.len(), 5);
    }

    #[test]
    fn panic_releases_every_key_and_the_pedal() {
        let mut engine = Engine::default();
        engine.note_on(60, 90, 0, 0, 0, &mut |_| true);
        let sent = collect(|send| engine.panic(0, &mut |m| send(m)));
        assert_eq!(sent.len(), 129);
        assert!(sent[..128].iter().all(|m| m.bytes[0] == 0x80 && m.bytes[2] == 0));
        assert_eq!(sent[128].data(), &[0xB0, 64, 0]);
        assert_eq!(engine.held_count(), 0);

        engine.note_on(60, 90, 0, 0, 0, &mut |_| true);
        engine.note_on(67, 90, 0, 0, 0, &mut |_| true);
        let released = collect(|send| engine.release_all(0, &mut |m| send(m)));
        assert_eq!(released.len(), 3); // two held notes and the pedal
    }
}
