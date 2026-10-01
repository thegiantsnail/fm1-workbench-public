"""Compare the deployed site with the local app/ folder and with a git ref (default origin/main).
Usage: python check_live.py [git-ref] [url]"""
import hashlib, pathlib, subprocess, sys, urllib.request

REF = sys.argv[1] if len(sys.argv) > 1 else 'origin/main'
URL = (sys.argv[2] if len(sys.argv) > 2 else 'https://fm1-workbench.web.app').rstrip('/')
SKIP = {'test_dx7.cjs'}
sha = lambda b: hashlib.sha256(b).hexdigest()

in_ref = set(subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', REF, 'app'], text=True).split())
local = {p.relative_to('app').as_posix() for p in pathlib.Path('app').rglob('*') if p.is_file()}
names = sorted((local | {n[4:] for n in in_ref}) - SKIP)
ok = True
print(f'{"file":26s} {"local":9s} {REF:12s}')
for rel in names:
    try:
        live = sha(urllib.request.urlopen(f'{URL}/{rel}', timeout=30).read())
    except Exception:
        live = None
    lp = pathlib.Path('app', rel)
    loc = sha(lp.read_bytes()) if lp.exists() else None
    ref = sha(subprocess.check_output(['git', 'show', f'{REF}:app/{rel}'])) if f'app/{rel}' in in_ref else None
    cmp = lambda x: '=' if x == live else ('missing' if x is None else 'DIFFERENT')
    if live is None:
        a = b = 'not live'
    else:
        a, b = cmp(loc), cmp(ref)
    ok &= a == '=' and b == '='
    print(f'{rel:26s} {a:9s} {b:12s}')
print('\nlive site matches local and', REF if ok else f'-> MISMATCH (columns show how each copy compares to the live file)')
