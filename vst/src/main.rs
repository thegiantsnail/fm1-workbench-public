//! Standalone build: the same plugin outside a host, for testing against the hardware.

fn main() {
    nih_plug::nih_export_standalone::<fm1_vst::Fm1>();
}
