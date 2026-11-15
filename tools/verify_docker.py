#!/usr/bin/env python3
"""End-to-end check of the Docker Home Organizer, run in a THROWAWAY container.

Run from the project root (same setup as verify_pwa.py, plus Docker running):
    .venv-test/bin/python tools/verify_docker.py

It builds the image from this folder and starts it on port 8281 with a temporary
COPY of data/organizer.db. Your real container and real data are never touched.

Checks: database upgrade (stable ids, order kept), adding, editing and deleting
events and tasks, notes, live sync between two open pages, export/import
(replace + merge, no duplicates), backups moving between the Docker app and the
offline app in both directions, and printing.

If a reference copy of the app is running at OLD_APP_URL (default
http://localhost:8280, your normal container), it also checks that the display and
printed month are pixel-identical to it. Run this BEFORE redeploying to compare
against the previous version.
"""
import json, os, shutil, subprocess, sys, time, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'tools' / 'test-output' / 'docker'
shutil.rmtree(OUT, ignore_errors=True); OUT.mkdir(parents=True)
PORT, PWA_PORT = 8281, 8766
NEW = f'http://localhost:{PORT}'
PWA = f'http://localhost:{PWA_PORT}/'
OLD = os.environ.get('OLD_APP_URL', 'http://localhost:8280')
IMAGE, NAME = 'home-organizer-test', 'home-organizer-test'
results = []

def check(name, cond, detail=''):
    results.append((name, bool(cond)))
    print(('PASS ' if cond else 'FAIL ') + name + (f'  [{detail}]' if detail and not cond else ''))

def api(path, method='GET', body=None):
    req = urllib.request.Request(NEW + '/api/' + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b'{}')

def up(url):
    try:
        urllib.request.urlopen(url, timeout=2); return True
    except Exception:
        return False

# ---- throwaway container with a copy of the real database ----
# inside the project: Docker Desktop on a Mac can't see the system temp folder
data_dir = OUT / 'data'; data_dir.mkdir()
real_db = ROOT / 'data' / 'organizer.db'
if real_db.exists():
    subprocess.run(['sqlite3', str(real_db), f'.backup {data_dir / "organizer.db"}'], check=True)
original_rows = []
if (data_dir / 'organizer.db').exists():
    out = subprocess.run(['sqlite3', '-json', str(data_dir / 'organizer.db'), 'SELECT id, day, time, title FROM events ORDER BY day, time, id'],
                         capture_output=True, text=True).stdout
    original_rows = json.loads(out or '[]')
# what the offline-app migration script produces from the same (not yet upgraded) database
subprocess.run([sys.executable, str(ROOT / 'tools' / 'export_for_pwa.py'), str(data_dir / 'organizer.db'), str(OUT / 'migrated.json')],
               check=True, capture_output=True)

subprocess.run(['docker', 'rm', '-f', NAME], capture_output=True)
subprocess.run(['docker', 'build', '-q', '-t', IMAGE, str(ROOT)], check=True, capture_output=True)
subprocess.run(['docker', 'run', '-d', '--name', NAME, '-p', f'{PORT}:8280', '-v', f'{data_dir}:/app/data', IMAGE], check=True, capture_output=True)
pwa_srv = subprocess.Popen([sys.executable, '-m', 'http.server', str(PWA_PORT), '--bind', '127.0.0.1', '--directory', str(ROOT / 'pwa')],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(60):
    if up(NEW + '/api/state'): break
    time.sleep(0.5)
if not (data_dir / 'organizer.db').exists():
    sys.exit('Test container did not use the test data folder; stopping.')

errors = []
try:
    # ---- database upgrade ----
    _, s = api('state')
    migrated = json.loads((OUT / 'migrated.json').read_text())['data']['events']
    check('existing events kept after upgrade', len(s['events']) == len(original_rows))
    check('existing order kept', [e['title'] for e in s['events']] == [r['title'] for r in original_rows])
    check('upgrade ids match migration script ids', sorted(e['id'] for e in s['events']) == sorted(e['id'] for e in migrated))
    check('every event has created/updated', all(e['created'] and e['updated'] for e in s['events']))
    # restart: upgrade must not run twice or change anything
    subprocess.run(['docker', 'restart', NAME], check=True, capture_output=True)
    for _ in range(60):
        if up(NEW + '/api/state'): break
        time.sleep(0.5)
    _, s2 = api('state')
    check('restart changes nothing', [{k: v for k, v in e.items()} for e in s2['events']] == s['events'])

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={'width': 1280, 'height': 900}, accept_downloads=True, timezone_id='America/Chicago')
        ctx.on('weberror', lambda e: errors.append(str(e.error)))
        dialogs = []
        def on_dialog(d):
            dialogs.append(d.message); d.accept()
        ctl = ctx.new_page(); ctl.on('dialog', on_dialog)
        # (the server's 400 for the deliberately bad backup file is expected, not an error)
        ctl.on('console', lambda m: m.type == 'error' and 'status of 400' not in m.text and errors.append(m.text))
        disp = ctx.new_page()
        disp.goto(NEW + '/display'); ctl.goto(NEW + '/control')
        ctl.wait_for_function('state.build'); disp.wait_for_function('state.build')
        today = ctl.evaluate('localDay()')
        check('pages use the server data layer', ctl.evaluate("typeof DB.exportData==='function' && !('validateBackup' in DB)"))

        # ---- events / tasks / notes through the UI, display updates live ----
        n0 = len(s['events'])
        ctl.fill('#event-day', today); ctl.fill('#event-time', '14:30'); ctl.fill('#event-title', 'Docker test')
        ctl.select_option('#event-color', 'purple'); ctl.click('#event-submit')
        ctl.wait_for_function(f'state.events.length==={n0 + 1}')
        disp.click('#today')
        disp.wait_for_function("document.querySelector('#agenda').textContent.includes('Docker test')", timeout=5000)
        check('add event; other open page updates live', True)
        ev = next(e for e in ctl.evaluate('state.events') if e['title'] == 'Docker test')
        check('new event has a UUID id', len(ev['id']) == 36 and ev['created'] == ev['updated'])
        ctl.click(f'[data-edit-event="{ev["id"]}"]'); ctl.fill('#event-title', 'Docker test edited'); ctl.click('#event-submit')
        ctl.wait_for_function("state.events.some(e=>e.title==='Docker test edited')")
        ev2 = next(e for e in ctl.evaluate('state.events') if e['id'] == ev['id'])
        check('edit event updates `updated` only', ev2['updated'] > ev['updated'] and ev2['created'] == ev['created'])
        ctl.fill('#task-title', 'Docker task'); ctl.click('#task-form button[type=submit]')
        ctl.wait_for_function("state.tasks.some(t=>t.title==='Docker task')")
        tid = next(t['id'] for t in ctl.evaluate('state.tasks') if t['title'] == 'Docker task')
        ctl.check(f'#tasks [data-task="{tid}"]'); ctl.wait_for_function(f"state.tasks.find(t=>t.id==='{tid}').done===true")
        check('add + tick task', True)
        ctl.fill('#notes', 'Docker notes'); ctl.click('#save-notes'); ctl.wait_for_function("state.notes==='Docker notes'")
        check('save notes', True)
        code, body = api('events', 'POST', {'day': '2026-02-30', 'title': 'x'})
        check('server rejects invalid date', code == 422)
        code, body = api('events', 'POST', {'day': '2026-03-01', 'time': '2 pm', 'title': '  spaced  '})
        st = api('state')[1]
        check('server normalizes time + trims title', any(e['time'] == '14:00' and e['title'] == 'spaced' for e in st['events']))
        sp = next(e for e in st['events'] if e['title'] == 'spaced')
        ctl.wait_for_function(f"state.events.some(e=>e.id==='{sp['id']}')")
        ctl.evaluate(f"DB.deleteEvent('{sp['id']}')"); ctl.wait_for_function(f"!state.events.some(e=>e.id==='{sp['id']}')")
        check('delete event', True)

        # ---- export (Backup panel) ----
        with ctl.expect_download() as dl:
            ctl.click('#export-data')
        exp_path = OUT / 'docker-export.json'; dl.value.save_as(exp_path)
        exp = json.loads(exp_path.read_text())
        check('export file name + format', dl.value.suggested_filename.startswith('home-organizer-backup-') and exp['app'] == 'home-organizer'
              and exp['schemaVersion'] == 1 and len(exp['data']['events']) == len(ctl.evaluate('state.events')), dl.value.suggested_filename)
        before = api('state')[1]

        # ---- merge same file: no change ----
        ctl.set_input_files('#import-file', str(exp_path)); ctl.select_option('#import-mode', 'merge'); ctl.click('#import-data')
        ctl.wait_for_function("document.querySelector('#backup-status').textContent.startsWith('Merged')")
        after = api('state')[1]
        check('merge own export: no duplicates/no change', {k: after[k] for k in ('events', 'tasks', 'notes')} == {k: before[k] for k in ('events', 'tasks', 'notes')},
              ctl.inner_text('#backup-status'))

        # ---- replace: wipe then restore ----
        empty = {'app': 'home-organizer', 'schemaVersion': 1, 'exportedAt': '2026-01-01T00:00:00.000Z', 'data': {'events': [], 'tasks': [], 'notes': {'content': ''}}}
        api('import', 'POST', {'mode': 'replace', 'backup': empty})
        check('replace with empty file wipes data', api('state')[1]['events'] == [])
        ctl.set_input_files('#import-file', str(exp_path)); ctl.select_option('#import-mode', 'replace'); ctl.click('#import-data')
        ctl.wait_for_function("document.querySelector('#backup-status').textContent.startsWith('Replaced')")
        restored = api('state')[1]
        check('replace restores identical data', {k: restored[k] for k in ('events', 'tasks', 'notes', 'selected_day')} == {k: before[k] for k in ('events', 'tasks', 'notes', 'selected_day')})

        # ---- bad files ----
        dialogs.clear()
        bad = OUT / 'bad.json'; bad.write_text(json.dumps({'app': 'home-organizer', 'schemaVersion': 1, 'data': {'events': [{'id': 'a', 'day': '2026-13-01', 'title': 'x'}], 'tasks': []}}))
        ctl.set_input_files('#import-file', str(bad)); ctl.select_option('#import-mode', 'merge'); ctl.click('#import-data'); ctl.wait_for_timeout(800)
        check('invalid file rejected with message, nothing changed', dialogs and 'Event 1' in dialogs[-1] and api('state')[1]['events'] == restored['events'], str(dialogs))

        # ---- Docker -> offline app ----
        pg = ctx.new_page(); pg.on('dialog', lambda d: d.accept())
        pg.goto(PWA + 'control.html'); pg.wait_for_timeout(500)
        pg.evaluate("DB.addEvent({day:'2026-12-24',title:'Only on phone',color:'green'})")
        pg.set_input_files('#import-file', str(exp_path)); pg.select_option('#import-mode', 'merge'); pg.click('#import-data'); pg.wait_for_timeout(600)
        pstate = pg.evaluate('DB.snapshot()')
        check('Docker export merges into offline app', len(pstate['events']) == len(exp['data']['events']) + 1 and pstate['notes'] == 'Docker notes')
        pg.set_input_files('#import-file', str(exp_path)); pg.click('#import-data'); pg.wait_for_timeout(600)
        check('re-merge into offline app: no duplicates', pg.evaluate('DB.snapshot()') == pstate)

        # ---- offline app -> Docker ----
        with pg.expect_download() as dl:
            pg.click('#export-data')
        pwa_path = OUT / 'pwa-export.json'; dl.value.save_as(pwa_path)
        ctl.set_input_files('#import-file', str(pwa_path)); ctl.select_option('#import-mode', 'merge'); ctl.click('#import-data')
        ctl.wait_for_function("document.querySelector('#backup-status').textContent.startsWith('Merged')")
        dstate = api('state')[1]
        check('offline-app export merges into Docker (adds only the new event)', len(dstate['events']) == len(restored['events']) + 1
              and any(e['title'] == 'Only on phone' for e in dstate['events']), ctl.inner_text('#backup-status'))
        check('identical events in both apps after round trip', [e['id'] for e in dstate['events']] == [e['id'] for e in pstate['events']]
              and [(e['day'], e['time'], e['title'], e['updated']) for e in dstate['events']] == [(e['day'], e['time'], e['title'], e['updated']) for e in pstate['events']])
        # migration-script file merges without duplicates (same ids as the upgrade gave)
        ctl.set_input_files('#import-file', str(OUT / 'migrated.json')); ctl.click('#import-data')
        ctl.wait_for_function("document.querySelector('#backup-status').textContent.includes('0 new items')")
        check('migration-script file merges into Docker with no duplicates', len(api('state')[1]['events']) == len(dstate['events']), ctl.inner_text('#backup-status'))
        pg.close()

        # ---- print (now in-page, same as offline app) ----
        ctl.fill('#print-month', today[:7]); ctl.evaluate('window.print=()=>{}'); ctl.click('#print-month-calendar')
        check('print layout built', 'Docker test edited' in ctl.inner_text('#print-calendar'))
        ctx.close()

        # ---- pixel comparison with the reference app (same data) ----
        if up(OLD + '/api/state'):
            from PIL import Image, ImageChops
            # put the reference data into the test container
            ref = json.loads(urllib.request.urlopen(OLD + '/api/state').read())
            api('import', 'POST', {'mode': 'replace', 'backup': json.loads((OUT / 'migrated.json').read_text())})
            same_data = [(e['day'], e['time'], e['title']) for e in api('state')[1]['events']] == [(e['day'], e['time'], e['title']) for e in ref['events']]
            if not same_data:
                print('SKIP pixel comparison: reference app data differs from data/organizer.db')
            else:
                size = {'width': 1008, 'height': 768}
                shots = {}
                for label, base, ctl_path, disp_path in (('old', OLD, '/control', '/display'), ('new', NEW, '/control', '/display')):
                    c = browser.new_context(viewport={'width': 1280, 'height': 900}, timezone_id='America/Chicago'); pg = c.new_page()
                    pg.goto(base + disp_path); pg.wait_for_timeout(1500)
                    pg.screenshot(path=str(OUT / f'{label}-display.png'))
                    pg.goto(base + ctl_path); pg.wait_for_timeout(1000); pg.fill('#print-month', today[:7])
                    if pg.evaluate("!!document.querySelector('#print-calendar')"):  # in-page print
                        pg.set_viewport_size(size); pg.evaluate('window.print=()=>{}'); pg.click('#print-month-calendar')
                        pg.emulate_media(media='print'); pg.screenshot(path=str(OUT / f'{label}-print.png'))
                    else:  # older pop-up print
                        with c.expect_page() as pop:
                            pg.click('#print-month-calendar')
                        w = pop.value; w.wait_for_load_state(); w.set_viewport_size(size); w.emulate_media(media='print')
                        w.screenshot(path=str(OUT / f'{label}-print.png'))
                    c.close()
                for name in ('display', 'print'):
                    a = Image.open(OUT / f'old-{name}.png').convert('RGB'); b = Image.open(OUT / f'new-{name}.png').convert('RGB')
                    diff = ImageChops.difference(a, b).point(lambda v: 255 if v > 8 else 0).getbbox() if a.size == b.size else 'size'
                    check(f'{name} pixel-identical to reference app at {OLD}', diff is None, f'differs in {diff}')
        else:
            print(f'SKIP pixel comparison: nothing running at {OLD}')
        browser.close()
finally:
    pwa_srv.terminate()
    subprocess.run(['docker', 'rm', '-f', NAME], capture_output=True)
    shutil.rmtree(data_dir, ignore_errors=True)

check('no JS errors', not errors, str(errors[:5]))
failed = [r for r in results if not r[1]]
print(f'\n{len(results) - len(failed)}/{len(results)} passed')
sys.exit(1 if failed else 0)
