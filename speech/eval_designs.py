"""Compare voice designs / options of the speech planner with Whisper on the software FM-1 (typed-text path).

    python speech/eval_designs.py                    # all designs
    python speech/eval_designs.py --hw               # also on the FM-1 (identical MIDI)
"""
import json, re, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import resynth as R, eval_web as W

PHRASES = W.PHRASES + ['please call me later', 'the dog is barking', 'where are my keys', 'the music is too loud',
                       'thank you very much', 'it is raining outside', 'my name is sam', 'come here right now',
                       'this is a test', 'the sun is shining', 'we need more time', 'can you help me']
VARIANTS = {
    'harmonic': {},
    'lebrun': {'design': 'lebrun'},
    'sine': {'design': 'sine'},
    'fixedfm': {'design': 'fixedfm'},
    'harmonic+vib': {'vib': 12},
    'fixedfm+vib': {'design': 'fixedfm', 'vib': 12},
    'fixedfm_b46': {'design': 'fixedfm', 'bright': 46},
    'fixedfm_b62': {'design': 'fixedfm', 'bright': 62},
    'fixedfm_hop10': {'design': 'fixedfm', 'hop': 10},
    'fixedfm_hop30': {'design': 'fixedfm', 'hop': 30},
    'nosel_fb': {'select': False, 'fric': 'fb'},
    'sel_fb': {'select': True, 'fric': 'fb'},
    'nosel_band': {'select': False, 'fric': 'band'},
    'sel_band': {'select': True, 'fric': 'band'},
    'sel_band_noburst': {'select': True, 'fric': 'band', 'burst': False},
    'sel_band_d74': {'select': True, 'fric': 'band', 'fricDepth': 74},
    'sel_band_d86': {'select': True, 'fric': 'band', 'fricDepth': 86},
    'sel_band_hfv10': {'select': True, 'fric': 'band', 'hfVoiced': -10},
    'sel_band_vdb24': {'select': True, 'fric': 'band', 'vowelDb': -24},
}

if __name__ == '__main__':
    hw = '--hw' in sys.argv
    only = [a for a in sys.argv[1:] if not a.startswith('--')]
    res = {}
    for name, opts in VARIANTS.items():
        if only and name not in only: continue
        tot = W.run(opts, hw=hw, phrases=PHRASES, tag='_' + re.sub(r'\W+', '', name))
        res[name] = tot
    print()
    for k, t in res.items():
        print(f"{k:14} " + '  '.join(f"{src} {a}/{b} ({a / b:.0%})" for src, (a, b) in t.items() if b))
    (W.OUT / 'designs.json').write_text(json.dumps(res))
