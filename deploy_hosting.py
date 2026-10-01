"""Deploy app/ to Firebase Hosting using the gcloud login (no Firebase CLI needed).

    python deploy_hosting.py [--project PROJECT] [--account GOOGLE_ACCOUNT]

Project/account come from the flags, else FM1_FIREBASE_PROJECT / FM1_GCLOUD_ACCOUNT, else .deploy.local.json
({"project": ..., "account": ...}, git-ignored) - so no personal defaults live in the repo.

Steps (Firebase Hosting REST API): ensure Firebase is added to the project and the default site exists,
create a version, upload gzipped files by SHA-256, finalize, release. Re-run to redeploy; unchanged files are
not re-uploaded.
"""
import argparse, gzip, hashlib, json, pathlib, subprocess, sys, time, urllib.error, urllib.request

ROOT = pathlib.Path(__file__).parent
SKIP = {'test_dx7.cjs'}

# Security headers for every file. The app has no inline scripts/styles or third-party hosts; the timer Worker is a
# blob: URL and the favicon a data: URI. Web MIDI is allowed for this origin only.
SECURITY_HEADERS = {
    'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                               "connect-src 'self'; worker-src blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
    'Permissions-Policy': 'midi=(self), microphone=(self), camera=(), geolocation=()',
    'X-Content-Type-Options': 'nosniff',
    'Referrer-Policy': 'strict-origin-when-cross-origin',
    'Cross-Origin-Opener-Policy': 'same-origin',
}


def local_settings():
    import os
    cfg = {}
    p = ROOT / '.deploy.local.json'
    if p.exists():
        cfg = json.loads(p.read_text())
    return {'project': os.environ.get('FM1_FIREBASE_PROJECT') or cfg.get('project'),
            'account': os.environ.get('FM1_GCLOUD_ACCOUNT') or cfg.get('account')}
FB = 'https://firebase.googleapis.com/v1beta1'
HOST = 'https://firebasehosting.googleapis.com/v1beta1'


def gcloud_token(account):
    exe = 'gcloud.cmd' if sys.platform == 'win32' else 'gcloud'
    return subprocess.check_output([exe, 'auth', 'print-access-token', '--account', account], text=True).strip()


class Api:
    def __init__(self, token, project):
        self.h = {'Authorization': f'Bearer {token}', 'x-goog-user-project': project}

    def call(self, method, url, body=None, raw=None, ctype='application/json', ok404=False, ok409=False):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(url, data=data, method=method, headers={**self.h, 'Content-Type': ctype})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                txt = r.read().decode() or '{}'
                return json.loads(txt) if txt.strip().startswith('{') else {}
        except urllib.error.HTTPError as e:
            if (ok404 and e.code == 404) or (ok409 and e.code == 409):
                return None
            raise SystemExit(f'{method} {url} -> HTTP {e.code}: {e.read().decode()[:600]}')

    def wait(self, op):
        while not op.get('done'):
            time.sleep(2)
            op = self.call('GET', f"{FB}/{op['name']}")
        if 'error' in op:
            raise SystemExit(f"operation failed: {op['error']}")
        return op


def main():
    ap = argparse.ArgumentParser()
    ls = local_settings()
    ap.add_argument('--project', default=ls['project'])
    ap.add_argument('--account', default=ls['account'])
    ap.add_argument('--dir', default=str(ROOT / 'app'))
    a = ap.parse_args()
    if not a.project or not a.account:
        raise SystemExit('set --project/--account, FM1_FIREBASE_PROJECT/FM1_GCLOUD_ACCOUNT, or .deploy.local.json')
    api = Api(gcloud_token(a.account), a.project)

    if api.call('GET', f'{FB}/projects/{a.project}', ok404=True) is None:
        print('adding Firebase to project…')
        api.wait(api.call('POST', f'{FB}/projects/{a.project}:addFirebase', {}))
    site = a.project
    if api.call('GET', f'{HOST}/projects/{a.project}/sites/{site}', ok404=True) is None:
        print('creating hosting site…')
        api.call('POST', f'{HOST}/projects/{a.project}/sites?siteId={site}', {}, ok409=True)

    version = api.call('POST', f'{HOST}/sites/{site}/versions', {'config': {'headers': [
        {'glob': '**', 'headers': SECURITY_HEADERS},
        {'glob': '/library/**', 'headers': {'Cache-Control': 'public, max-age=3600'}},
        {'glob': '/speech/**', 'headers': {'Cache-Control': 'public, max-age=3600'}},
        {'glob': '**/*.@(js|css|html)', 'headers': {'Cache-Control': 'no-cache'}},
    ]}})['name']

    base = pathlib.Path(a.dir)
    files, blobs = {}, {}
    for p in sorted(base.rglob('*')):
        if p.is_file() and p.name not in SKIP:
            gz = gzip.compress(p.read_bytes(), mtime=0)
            h = hashlib.sha256(gz).hexdigest()
            files['/' + p.relative_to(base).as_posix()] = h
            blobs[h] = gz
    res = api.call('POST', f'{HOST}/{version}:populateFiles', {'files': files})
    need = res.get('uploadRequiredHashes', [])
    print(f'{len(files)} files, {len(need)} to upload ({sum(len(blobs[h]) for h in need) / 1e6:.1f} MB gzipped)')
    for h in need:
        api.call('POST', f"{res['uploadUrl']}/{h}", raw=blobs[h], ctype='application/octet-stream')
    api.call('PATCH', f'{HOST}/{version}?update_mask=status', {'status': 'FINALIZED'})
    api.call('POST', f'{HOST}/sites/{site}/releases?versionName={version}', {})
    print(f'released: https://{site}.web.app  (also https://{site}.firebaseapp.com)')


if __name__ == '__main__':
    main()
