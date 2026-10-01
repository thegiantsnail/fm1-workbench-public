# Notices

FM-1 Workbench and FM-1 Controller
Copyright (C) 2026 thegiantsnail

This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public
License, version 3, as published by the Free Software Foundation. It is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See
[LICENSE](LICENSE) for the full text.

## Not affiliated

This is an independent project. It is not affiliated with, endorsed by or supported by M-VAVE, Yamaha, Image-Line,
Microsoft, Steinberg or Baud Girl. "FM-1", "DX7", "FL Studio", "VST" and other names are trademarks of their owners and
are used only to say what the software works with. Third-party firmware is not included; links to it are for reference.

## Data included in this repository

### CMU Pronouncing Dictionary (`app/speech/cmudict.txt`, and the stress pairs in `app/speech/pos.json`)

`cmudict.txt` is the CMU Pronouncing Dictionary re-encoded in a compact form by `speech/build_units.py`. Its licence:

```
Copyright (C) 1993-2015 Carnegie Mellon University. All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions
are met:

1. Redistributions of source code must retain the above copyright
   notice, this list of conditions and the following disclaimer.
   The contents of this file are deemed to be source code.

2. Redistributions in binary form must reproduce the above copyright
   notice, this list of conditions and the following disclaimer in
   the documentation and/or other materials provided with the
   distribution.

This work was supported in part by funding from the Defense Advanced
Research Projects Agency, the Office of Naval Research and the National
Science Foundation of the United States of America, and by member
companies of the Carnegie Mellon Sphinx Speech Consortium. We acknowledge
the contributions of many volunteers to the expansion and improvement of
this dictionary.

THIS SOFTWARE IS PROVIDED BY CARNEGIE MELLON UNIVERSITY ``AS IS'' AND
ANY EXPRESSED OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO,
THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR
PURPOSE ARE DISCLAIMED.  IN NO EVENT SHALL CARNEGIE MELLON UNIVERSITY
NOR ITS EMPLOYEES BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

### Brown Corpus (`app/speech/pos.json`)

`pos.json` holds the parameters of a small part-of-speech tagger (tag transition and word-tag statistics) computed by
`speech/build_pos.py` from the Brown Corpus (W. N. Francis and H. Kučera, Brown University), as distributed with NLTK.
It contains counts and probabilities, not the corpus text. The corpus is distributed for non-commercial research use;
anyone with stricter needs should rebuild `pos.json` from a corpus whose terms suit them.

### Speech units (`app/speech/units.json`)

`units.json` holds acoustic measurements (energy, voicing, formant frequencies and levels per 10 ms) of English diphones,
made by `speech/build_units.py` from speech produced by the Windows SAPI voice "Microsoft David". It contains no audio.
The voice itself belongs to Microsoft and is subject to its licence terms; the measurements are included for use with
this project, and can be rebuilt from another voice with the same script.

## Data not included

DX7 patch banks are not distributed with this project: most collections in circulation have no clear redistribution
terms, and factory voices belong to their manufacturers. See "Voices" in [README.md](README.md) for where to find
patches and how to build a library from your own. Some Android unit tests compare the Kotlin code with vectors made
from a 32-voice factory test bank (`ROM1A.syx`, `rom1a_vced.txt`, `bank_sysex.txt`, `macro_vectors.txt`,
`synth_vectors.json` under `android/app/src/test/resources/`). Those files are used in development only and are not
part of the public release; the tests that need them skip when they are absent.

## Software the plugin in `vst/` is built with

The plugin links these, among the crates listed in `vst/Cargo.lock`, each under its own licence:

| Component | Licence |
|---|---|
| [NIH-plug](https://github.com/robbert-vdh/nih-plug) and its egui adapter | ISC |
| [vst3-sys](https://github.com/RustAudio/vst3-sys), the VST3 bindings | GPL-3.0 |
| [QuickJS](https://bellard.org/quickjs/) through [rquickjs](https://github.com/DelSkayn/rquickjs) | MIT |
| [egui](https://github.com/emilk/egui), [baseview](https://github.com/RustAudio/baseview) | MIT or Apache-2.0 |
| [coremidi](https://github.com/chris-zen/coremidi), [rtrb](https://github.com/mgeier/rtrb), [serde](https://serde.rs) | MIT or Apache-2.0 |

Because the VST3 bindings are under the GPL version 3, a distributed VST3 build of the plugin is too, which is also this
project's licence. The Android build uses the Gradle wrapper (Apache-2.0).
