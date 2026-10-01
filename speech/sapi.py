"""Windows SAPI (System.Speech) text-to-speech with phoneme timings, offline and built in.

say_batch([(text, wav_path), ...]) -> [[(phone, start_ms, dur_ms), ...], ...]   phones in ARPAbet (lowercase), '_' = pause
"""
import pathlib, subprocess, tempfile, json

# SAPI 5 American English phone ids (verified: "hello world" -> _ h eh l ow w er r l d)
SAPI = {7: '_', 10: 'aa', 11: 'ae', 12: 'ah', 13: 'ao', 14: 'aw', 15: 'ax', 16: 'ay', 17: 'b', 18: 'ch', 19: 'd', 20: 'dh',
        21: 'eh', 22: 'er', 23: 'ey', 24: 'f', 25: 'g', 26: 'hh', 27: 'ih', 28: 'iy', 29: 'jh', 30: 'k', 31: 'l', 32: 'm',
        33: 'n', 34: 'ng', 35: 'ow', 36: 'oy', 37: 'p', 38: 'r', 39: 's', 40: 'sh', 41: 't', 42: 'th', 43: 'uh', 44: 'uw',
        45: 'v', 46: 'w', 47: 'y', 48: 'z', 49: 'zh'}

PS = r'''
Add-Type -AssemblyName System.Speech
Add-Type -ReferencedAssemblies System.Speech -TypeDefinition @"
using System; using System.IO; using System.Text; using System.Speech.Synthesis; using System.Speech.AudioFormat;
public static class SapiBatch {
  public static void Run(string jobs, int rate) {
    var outp = new StringBuilder();
    foreach (var line in File.ReadAllLines(jobs + ".in", Encoding.UTF8)) {
      var parts = line.Split('\t');
      var sb = new StringBuilder();
      using (var s = new SpeechSynthesizer()) {
        s.Rate = rate;
        s.SetOutputToWaveFile(parts[1], new SpeechAudioFormatInfo(22050, AudioBitsPerSample.Sixteen, AudioChannel.Mono));
        s.PhonemeReached += (o, e) => sb.Append(((int)e.Phoneme[0]) + "," + e.AudioPosition.TotalMilliseconds + "," + e.Duration.TotalMilliseconds + ";");
        s.Speak(parts[0]);
      }
      outp.AppendLine(sb.ToString());
    }
    File.WriteAllText(jobs + ".out", outp.ToString());
  }
}
"@
[SapiBatch]::Run($args[0], [int]$args[1])
'''


def say_batch(jobs, rate=-2):
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / 'jobs'
        base.with_suffix('.in').write_text('\n'.join(f'{t}\t{p}' for t, p in jobs), encoding='utf-8')
        ps = pathlib.Path(d) / 'sapi.ps1'; ps.write_text(PS, encoding='utf-8')
        subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(ps), str(base), str(rate)],
                       check=True, capture_output=True)
        lines = base.with_suffix('.out').read_text(encoding='utf-8').splitlines()
    out = []
    for ln in lines:
        ev = []
        for item in filter(None, ln.split(';')):
            c, s, dur = item.split(',')
            if int(c) in SAPI:
                ev.append((SAPI[int(c)], float(s), float(dur)))
        out.append(ev)
    return out


if __name__ == '__main__':
    import sys
    print(say_batch([(' '.join(sys.argv[1:]) or 'hello world', str(pathlib.Path(tempfile.gettempdir()) / 'sapi_test.wav'))]))
