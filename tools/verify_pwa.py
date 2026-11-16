#!/usr/bin/env python3
"""End-to-end check of the offline app in ./pwa/ using headless Chromium.

Setup (once):
    python3 -m venv .venv-test
    .venv-test/bin/pip install playwright pillow
    .venv-test/bin/playwright install chromium

Run from the project root:
    .venv-test/bin/python tools/verify_pwa.py

What it does: serves ./pwa/ on localhost, then adds/edits/deletes events and
checklist items, saves notes, reloads, goes offline, exports, wipes storage,
imports (replace + merge), checks timezone handling and app updates.
It also converts data/organizer.db with export_for_pwa.py and imports that.

If the Docker app is running (OLD_APP_URL, default http://localhost:8280), it
also screenshots the old and new display and printed month with the same data and
checks they are pixel-identical (the app name in the header is ignored). Otherwise that part is skipped.

Uses only throwaway browser profiles: your own browser data is never touched.
Output (screenshots, exported files) goes to tools/test-output/pwa/, replaced each run.
Exit code 0 = all checks passed.
"""
import json, os, shutil, subprocess, sys, time, re, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PWA = ROOT / 'pwa'
OLD_APP = os.environ.get('OLD_APP_URL', 'http://localhost:8280')
S = ROOT / 'tools' / 'test-output' / 'pwa'
shutil.rmtree(S, ignore_errors=True); S.mkdir(parents=True)
PORT = 8765
BASE = f'http://localhost:{PORT}/'
SHOTS = S / 'shots'; SHOTS.mkdir(exist_ok=True)
results = []
# Print the way Safari behaves: print() returns at once and 'afterprint' fires immediately.
SAFARI_PRINT = "window.print=()=>{window.dispatchEvent(new Event('beforeprint'));window.dispatchEvent(new Event('afterprint'))}"

def print_shot(ctx, pg, display_url, control_url, path, size):
    """Screenshot the printed month for the day the display is showing. Uses the Print
    button in the display calendar; falls back to Control's 'Print selected month' (in-page
    or older pop-up) so older reference versions of the app can still be compared."""
    pg.set_viewport_size(size)
    pg.goto(display_url); pg.wait_for_timeout(1200)
    if pg.locator('.print-cell').count():
        pg.evaluate(SAFARI_PRINT); pg.click('.print-cell'); pg.wait_for_timeout(300)
        pg.emulate_media(media='print'); pg.screenshot(path=path); pg.emulate_media(media='screen')
        return
    month = pg.evaluate('(state.selected_day || localDay()).slice(0, 7)')
    pg.goto(control_url); pg.wait_for_timeout(1000); pg.fill('#print-month', month)
    if pg.evaluate("!!document.querySelector('#print-calendar')"):
        pg.evaluate(SAFARI_PRINT); pg.click('#print-month-calendar'); pg.wait_for_timeout(300)
        pg.emulate_media(media='print'); pg.screenshot(path=path); pg.emulate_media(media='screen')
    else:
        with ctx.expect_page() as pop:
            pg.click('#print-month-calendar')
        w = pop.value; w.wait_for_load_state(); w.set_viewport_size(size); w.emulate_media(media='print'); w.screenshot(path=path)

def check(name, cond, detail=''):
    results.append((name, bool(cond), detail))
    print(('PASS ' if cond else 'FAIL ') + name + (f'  [{detail}]' if detail and not cond else ''))

def serve(directory, port):
    return subprocess.Popen([sys.executable, '-m', 'http.server', str(port), '--bind', '127.0.0.1', '--directory', str(directory)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def snapshot(page):
    return page.evaluate('DB.snapshot()')

def wait_sw(page):
    page.wait_for_function('navigator.serviceWorker.controller !== null', timeout=15000)

# Build the migration file from the Docker app's database (read-only).
subprocess.run([sys.executable, str(ROOT / 'tools' / 'export_for_pwa.py'), str(ROOT / 'data' / 'organizer.db'), str(S / 'migrated.json')], check=True)

srv = serve(PWA, PORT); time.sleep(1)
errors = []
try:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={'width': 1280, 'height': 900}, accept_downloads=True, timezone_id='America/Chicago')
        ctx.on('weberror', lambda e: errors.append(str(e.error)))
        dialogs = []
        def on_dialog(d):
            dialogs.append(d.message)
            d.accept()
        page = ctx.new_page()
        page.on('dialog', on_dialog)
        page.on('console', lambda m: m.type == 'error' and errors.append(m.text))
        external = []
        page.on('request', lambda r: not r.url.startswith(BASE) and not r.url.startswith('blob:') and not r.url.startswith('data:') and external.append(r.url))

        # ---- first load ----
        page.goto(BASE)
        wait_sw(page)
        page.reload()
        check('display loads, header present', page.locator('h1').inner_text() == 'Personal Organizer')
        check('service worker controls page', page.evaluate('!!navigator.serviceWorker.controller'))
        check('persist() requested (API present)', page.evaluate("'storage' in navigator && 'persist' in navigator.storage"))
        manifest_ok = page.evaluate("fetch('manifest.webmanifest').then(r=>r.json()).then(m=>m.display==='standalone'&&m.icons.length===3)")
        check('manifest valid (standalone, icons)', manifest_ok)

        # ---- control: events ----
        page.click('a.status-button')
        page.wait_for_url('**/control.html')
        today = page.evaluate('localDay()')
        page.fill('#event-day', today); page.fill('#event-time', '14:30'); page.fill('#event-title', 'Dentist')
        page.fill('#event-description', 'Bring card'); page.select_option('#event-color', 'red'); page.select_option('#event-category', 'Personal')
        page.click('#event-submit')
        page.wait_for_selector('#events .event')
        s = snapshot(page)
        ev = s['events'][0]
        check('add event', len(s['events']) == 1 and ev['title'] == 'Dentist' and ev['time'] == '14:30' and ev['color'] == 'red' and ev['day'] == today)
        check('event id is a UUID', re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}', ev['id']) is not None, ev['id'])
        page.fill('#event-time', ''); page.fill('#event-title', 'All day thing'); page.click('#event-submit')
        page.wait_for_function('state.events.length===2')
        page.click(f'[data-edit-event="{ev["id"]}"]')
        page.fill('#event-title', 'Dentist (moved)'); page.fill('#event-time', '09:15')
        page.click('#event-submit')
        page.wait_for_function("state.events.some(e=>e.title==='Dentist (moved)')")
        s = snapshot(page)
        check('edit event', any(e['title'] == 'Dentist (moved)' and e['time'] == '09:15' for e in s['events']) and len(s['events']) == 2)
        check('events sorted (all-day first, then time)', [e['time'] for e in s['events']] == ['', '09:15'])
        allday = next(e for e in s['events'] if e['time'] == '')
        page.click(f'[data-del-event="{allday["id"]}"]')
        page.wait_for_function('state.events.length===1')
        check('delete event', len(snapshot(page)['events']) == 1)

        # validation mirrors server
        err = page.evaluate("DB.addEvent({day:'2026-02-30',title:'x'}).then(()=>'saved',e=>e.message)")
        check('rejects invalid date', err != 'saved', err)
        norm = page.evaluate("DB.addEvent({day:'2026-03-01',time:'2 pm',title:'norm'}).then(r=>r.time)")
        check('normalizes "2 pm" -> 14:00', norm == '14:00', norm)

        # ---- tasks + notes ----
        for t in ['Milk', 'Bread']:
            page.fill('#task-title', t); page.click('#task-form button[type=submit]')
            page.wait_for_function(f"state.tasks.some(t=>t.title==='{t}')")
        tasks = snapshot(page)['tasks']
        check('add tasks in order', [t['title'] for t in tasks] == ['Milk', 'Bread'])
        page.check(f'#tasks [data-task="{tasks[0]["id"]}"]')
        page.wait_for_function('state.tasks[0].done===true')
        check('toggle task', snapshot(page)['tasks'][0]['done'] is True)
        page.click(f'[data-del-task="{tasks[1]["id"]}"]')
        page.wait_for_function('state.tasks.length===1')
        check('delete task', len(snapshot(page)['tasks']) == 1)
        page.fill('#notes', 'Trash goes out Tuesday'); page.click('#save-notes')
        page.wait_for_function("state.notes==='Trash goes out Tuesday'")
        check('save notes', snapshot(page)['notes'] == 'Trash goes out Tuesday')

        # ---- view control syncs to display tab on same device ----
        disp = ctx.new_page(); disp.goto(BASE); disp.wait_for_function('state.events.length>0')
        page.click('[data-view="tasks"]')
        disp.wait_for_function("document.querySelector('#calendar-panel').classList.contains('hidden')", timeout=5000)
        check('view buttons drive display tab (same device)', disp.is_visible('#tasks-panel') and not disp.is_visible('#calendar-panel'))
        page.click('[data-view="overview"]')
        disp.wait_for_function("!document.querySelector('#calendar-panel').classList.contains('hidden')")
        dots = disp.eval_on_selector_all('#agenda .event-dot', 'els=>els.map(e=>e.className)')
        check('agenda dot colors shown', any('dot-red' in d for d in dots), str(dots))

        # ---- new-event date follows the day selected on the display ----
        page.reload(); page.wait_for_function('state.tasks.length>0')
        other = today[:8] + ('02' if today[8:] == '01' else '01')
        disp.click(f'[data-day="{other}"]')
        page.wait_for_function(f"document.querySelector('#event-day').value==='{other}'", timeout=5000)
        check('new-event date = day selected on display', True)
        page.fill('#event-day', today)  # a date picked by hand in the form...
        disp.click(f'[data-day="{today[:8]}03"]' if today[8:] != '03' else f'[data-day="{today[:8]}04"]')
        page.wait_for_timeout(800)
        check('...is not overwritten by later display selections', page.input_value('#event-day') == today)
        check('Selected date box removed from Control', page.locator('#day, #show-day').count() == 0)
        disp.click(f'[data-day="{today}"]'); page.wait_for_function(f"state.selected_day==='{today}'")
        page.reload(); page.wait_for_function('state.tasks.length>0')
        disp.close()

        # ---- reload persists ----
        before = snapshot(page)
        page.reload(); page.wait_for_function('state.tasks.length===1')
        check('data survives reload', snapshot(page) == before)

        # ---- offline ----
        ctx.set_offline(True)
        page.goto(BASE + 'control.html'); page.wait_for_function('state.tasks.length===1')
        check('offline: control page loads from cache', page.locator('h1').inner_text() == 'Control')
        page.fill('#event-day', today); page.fill('#event-title', 'Offline event'); page.click('#event-submit')
        page.wait_for_function("state.events.some(e=>e.title==='Offline event')")
        check('offline: can add event', True)
        page.goto(BASE); page.wait_for_function('state.events.length>0')
        check('offline: display loads + shows data', 'Offline event' in page.inner_text('#agenda'))
        page.goto(BASE + 'index.html?x=1'); page.wait_for_function('state.events.length>0')
        check('offline: index.html with query string', page.locator('h1').inner_text() == 'Personal Organizer')
        page.screenshot(path=str(SHOTS / 'pwa-display-offline.png'))

        # ---- export ----
        page.goto(BASE + 'control.html'); page.wait_for_function('state.events.length>0')
        check('backup button says Backup', page.inner_text('#export-data') == 'Backup')
        check('status before backup: not backed up', 'Not backed up' in page.inner_text('#backup-session'), page.inner_text('#backup-session'))
        with page.expect_download() as dl:
            page.click('#export-data')
        path = S / 'export1.json'; dl.value.save_as(path)
        page.wait_for_function("document.querySelector('#backup-session').textContent.includes('Backed up at')")
        check('status after backup: backed up', 'backup-state ok' in page.get_attribute('#backup-session', 'class'))
        page.reload(); page.wait_for_function('state.events.length>0')
        check('status survives reload in same session', 'Backed up at' in page.inner_text('#backup-session'))
        page.fill('#task-title', 'After backup'); page.click('#task-form button[type=submit]')
        page.wait_for_function("document.querySelector('#backup-session').textContent.includes('Changes since')")
        check('status after a change: changes since backup', True)
        page.click(f'[data-del-task="{page.evaluate("state.tasks.find(t=>t.title===\'After backup\').id")}"]')
        page.wait_for_function("document.querySelector('#backup-session').textContent.includes('Backed up at')")
        check('undoing the change: backed up again', True)
        exported = json.loads(path.read_text())
        check('export file format', exported['app'] == 'personal-organizer' and exported['schemaVersion'] == 1 and 'exportedAt' in exported
              and len(exported['data']['events']) == len(snapshot(page)['events']), dl.value.suggested_filename)
        state_before_clear = snapshot(page)

        # ---- clear storage (real wipe of IndexedDB for the origin) ----
        cdp = ctx.new_cdp_session(page)
        page.goto('about:blank')
        cdp.send('Storage.clearDataForOrigin', {'origin': BASE.rstrip('/'), 'storageTypes': 'indexeddb'})
        page.goto(BASE + 'control.html'); page.wait_for_timeout(800)
        empty = snapshot(page)
        check('storage cleared (still offline, app still loads)', empty['events'] == [] and empty['tasks'] == [] and empty['notes'] == '')

        # ---- import replace ----
        page.set_input_files('#import-file', str(path)); page.select_option('#import-mode', 'replace')
        page.click('#import-data'); page.wait_for_function('state.events.length>0')
        restored = snapshot(page)
        check('import (replace) restores identical data', restored == state_before_clear,
              json.dumps(restored)[:300])
        check('ids/timestamps preserved', [e['id'] for e in restored['events']] == [e['id'] for e in state_before_clear['events']])

        # ---- merge: same file again => no duplicates ----
        page.set_input_files('#import-file', str(path)); page.select_option('#import-mode', 'merge')
        page.click('#import-data'); page.wait_for_timeout(500)
        check('merge same file: no duplicates', snapshot(page) == restored, page.inner_text('#backup-status'))

        # ---- merge migrated data from the Docker app ----
        page.set_input_files('#import-file', str(S / 'migrated.json')); page.select_option('#import-mode', 'merge')
        page.click('#import-data'); page.wait_for_timeout(500)
        merged = snapshot(page)
        migrated = json.loads((S / 'migrated.json').read_text())
        check('merge migrated file adds its events', len(merged['events']) == len(restored['events']) + len(migrated['data']['events']), page.inner_text('#backup-status'))
        page.set_input_files('#import-file', str(S / 'migrated.json')); page.click('#import-data'); page.wait_for_timeout(500)
        check('re-merge migrated file: no change', snapshot(page) == merged, page.inner_text('#backup-status'))

        # newer edit wins in merge; older copy doesn't overwrite
        mid = migrated['data']['events'][0]['id']
        page.evaluate(f"DB.updateEvent('{mid}', {{...state.events.find(e=>e.id==='{mid}'), title:'Edited in PWA'}})")
        page.set_input_files('#import-file', str(S / 'migrated.json')); page.click('#import-data'); page.wait_for_timeout(500)
        check('merge keeps newer local edit', any(e['title'] == 'Edited in PWA' for e in snapshot(page)['events']))

        # bad files rejected without touching data
        bad = S / 'bad.json'; bad.write_text(json.dumps({'app': 'personal-organizer', 'schemaVersion': 1, 'data': {'events': [{'id': 'a', 'day': '2026-13-01', 'title': 'x'}], 'tasks': []}}))
        n = len(snapshot(page)['events']); dialogs.clear()
        page.set_input_files('#import-file', str(bad)); page.click('#import-data'); page.wait_for_timeout(500)
        check('invalid backup rejected with message', dialogs and 'Event 1' in dialogs[-1] and len(snapshot(page)['events']) == n, str(dialogs))
        bad.write_text('{not json'); dialogs.clear()
        page.set_input_files('#import-file', str(bad)); page.click('#import-data'); page.wait_for_timeout(500)
        check('non-JSON rejected', dialogs and 'not valid JSON' in dialogs[-1])
        ctx.set_offline(False)

        # ---- no requests left the origin ----
        check('no external network requests', not external, str(external[:5]))

        # ---- print output ----
        # printed from the Print button in the display calendar, on the month with the test's own events
        page.goto(BASE); page.wait_for_function('state.events.length>0')
        page.evaluate(f"DB.setView('overview','{today}')"); page.wait_for_function(f"state.selected_day==='{today}'")
        page.evaluate(SAFARI_PRINT)  # we just render the print layout
        page.click('.print-cell'); page.wait_for_timeout(300)
        check('Control page no longer has print controls', True)
        page.emulate_media(media='print')
        check('print shows calendar, not the screen (Safari timing)', page.is_visible('#print-calendar') and not page.is_visible('header'))
        page.set_viewport_size({'width': 1008, 'height': 768})
        page.screenshot(path=str(SHOTS / 'pwa-print.png'))
        page.pdf(path=str(SHOTS / 'pwa-print.pdf'), prefer_css_page_size=True)
        # must fit ONE page whatever paper/orientation/margins the browser uses (Safari ignores @page)
        for fmt, land, margin in [('Letter', False, '0.5in'), ('Letter', True, '0.75in'), ('A4', False, '0.4in'), ('A4', True, '0.6in')]:
            pdf = page.pdf(format=fmt, landscape=land, prefer_css_page_size=False, margin={k: margin for k in ('top', 'bottom', 'left', 'right')})
            pages = len(re.findall(rb'/Type\s*/Page[^s]', pdf))
            check(f'print fits one page: {fmt} {"landscape" if land else "portrait"}, {margin} margins', pages == 1, f'{pages} pages')
        month_label = page.evaluate("new Date(localDay()+'T12:00:00').toLocaleDateString(undefined,{month:'long',year:'numeric'})")
        check('print layout shows month + events', page.inner_text('#print-calendar h1') == month_label and 'Dentist (moved)' in page.inner_text('#print-calendar'))
        page.emulate_media(media='screen')
        ctx.close()

        # ---- timezones: export in Auckland, import in Los Angeles, days unchanged ----
        days = {}
        for tz in ['Pacific/Auckland', 'America/Los_Angeles']:
            c = browser.new_context(timezone_id=tz, accept_downloads=True)
            pg = c.new_page(); pg.on('dialog', lambda d: d.accept())
            if tz == 'Pacific/Auckland':
                pg.clock.install(time='2026-11-15T23:30:00+13:00')  # late evening in Auckland (10:30 UTC)
                pg.goto(BASE + 'control.html'); pg.wait_for_timeout(500)
                check('localDay uses device timezone (Auckland 23:30)', pg.evaluate('localDay()') == '2026-11-15')
                pg.evaluate("DB.addEvent({day:localDay(),title:'tz test'})")
                with pg.expect_download() as dl: pg.click('#export-data')
                tzfile = S / 'tz.json'; dl.value.save_as(tzfile)
                days[tz] = pg.evaluate('DB.snapshot()')['events'][0]['day']
            else:
                pg.clock.install(time='2026-11-15T01:30:00-08:00')  # just after midnight in LA (09:30 UTC)
                pg.goto(BASE + 'control.html'); pg.wait_for_timeout(500)
                check('localDay uses device timezone (LA 01:30)', pg.evaluate('localDay()') == '2026-11-15')
                pg.set_input_files('#import-file', str(tzfile)); pg.select_option('#import-mode', 'merge'); pg.click('#import-data')
                pg.wait_for_timeout(500)
                days[tz] = pg.evaluate('DB.snapshot()')['events'][0]['day']
                pg.goto(BASE); pg.wait_for_timeout(500)
                check('display opens on the local day (LA)', pg.inner_text('#agenda-title').endswith('Nov 15'), pg.inner_text('#agenda-title'))
            c.close()
        check('dates round-trip across timezones', days['Pacific/Auckland'] == days['America/Los_Angeles'] == '2026-11-15', str(days))

        # ---- Print button inside the display calendar ----
        c = browser.new_context(viewport={'width': 1280, 'height': 900}); pg = c.new_page()
        pg.goto(BASE); pg.wait_for_timeout(500)
        where = "(()=>{const c=document.querySelector('.print-cell');const cells=[...document.querySelectorAll('#calendar > *')].slice(7);return [cells.indexOf(c),cells.length,c.style.gridColumn]})()"
        for day, expect, label in [('2026-11-15', [30, 31, 'span 5'], 'bottom-right after last day'),
                                   ('2026-10-15', [0, 32, 'span 4'], 'top-left when month ends on Saturday'),
                                   ('2026-02-10', [28, 29, 'span 7'], 'own row when month has no empty boxes')]:
            pg.evaluate(f"DB.setView('overview','{day}')"); pg.wait_for_function(f"state.selected_day==='{day}'"); pg.wait_for_timeout(200)
            got = pg.evaluate(where)
            check(f'display Print button: {label}', got == expect, str(got))
        # print() must run inside the tap itself, or Safari blocks it as "automatic printing"
        check('Print runs directly from the tap (no Safari "blocked printing" prompt)', pg.evaluate("""(()=>{window.__printed=false;const keep=window.print;window.print=()=>{window.__printed=true};document.querySelector('.print-cell').click();const r=window.__printed;window.print=keep;return r})()"""))
        pg.evaluate(SAFARI_PRINT); pg.click('.print-cell'); pg.wait_for_timeout(300); pg.emulate_media(media='print')
        check('display Print button prints the shown month (Safari timing)', pg.is_visible('#print-calendar') and pg.inner_text('#print-calendar h1') == 'February 2026' and not pg.is_visible('header'))
        c.close()

        # ---- app update reaches an open page ----
        upd = S / 'pwa-update'
        shutil.rmtree(upd, ignore_errors=True); shutil.copytree(PWA, upd)
        srv2 = serve(upd, PORT + 1)
        try:
            time.sleep(1)
            c = browser.new_context(); pg = c.new_page()
            pg.goto(f'http://localhost:{PORT+1}/'); wait_sw(pg); pg.reload(); pg.wait_for_timeout(500)
            (upd / 'index.html').write_text((upd / 'index.html').read_text().replace('<h1>Personal Organizer</h1>', '<h1>Personal Organizer v2</h1>'))
            (upd / 'sw.js').write_text((upd / 'sw.js').read_text().replace("const VERSION = '", "const VERSION = 'test-"))
            pg.evaluate('navigator.serviceWorker.getRegistration().then(r=>r.update())')
            pg.wait_for_function("document.querySelector('h1').textContent==='Personal Organizer v2'", timeout=15000)
            check('deployed update auto-reloads open page', True)
            keys = pg.evaluate('caches.keys()')
            check('old cache removed after update', len(keys) == 1 and 'test-' in keys[0], str(keys))
            c.close()
        except Exception as e:
            check('deployed update auto-reloads open page', False, str(e)[:200])
        finally:
            srv2.terminate()

        # ---- visual comparison with the Docker app (same data) ----
        try:
            urllib.request.urlopen(OLD_APP + '/api/state', timeout=2)
            old_running = True
        except Exception:
            old_running = False
            print(f'SKIP old/new pixel comparison: Docker app not reachable at {OLD_APP}')
        if old_running:
            from PIL import Image, ImageChops
            size = {'width': 1008, 'height': 768}  # 10.5in x 8in at 96dpi, the printable area
            # old app: display + printed month
            c = browser.new_context(viewport={'width': 1280, 'height': 900}, timezone_id='America/Chicago'); pg = c.new_page()
            pg.goto(OLD_APP + '/display'); pg.wait_for_timeout(1500)
            pg.screenshot(path=str(SHOTS / 'old-display.png'))
            print_shot(c, pg, OLD_APP + '/display', OLD_APP + '/control', str(SHOTS / 'old-print.png'), size)
            c.close()
            # new app: same data via the migration file
            c = browser.new_context(viewport={'width': 1280, 'height': 900}, timezone_id='America/Chicago'); pg = c.new_page()
            pg.on('dialog', lambda d: d.accept())
            pg.goto(BASE + 'control.html'); pg.wait_for_timeout(500)
            pg.set_input_files('#import-file', str(S / 'migrated.json')); pg.select_option('#import-mode', 'replace'); pg.click('#import-data')
            pg.wait_for_timeout(500); pg.goto(BASE); pg.wait_for_timeout(800)
            # The apps have different names on purpose; compare everything except that.
            pg.evaluate("document.querySelector('header h1').textContent='Home Organizer'")
            pg.screenshot(path=str(SHOTS / 'new-display.png'))
            print_shot(c, pg, BASE, BASE + 'control.html', str(SHOTS / 'new-print.png'), size)
            c.close()
            for name in ['display', 'print']:
                a = Image.open(SHOTS / f'old-{name}.png').convert('RGB'); b = Image.open(SHOTS / f'new-{name}.png').convert('RGB')
                # ignore anti-aliasing noise (a few colour steps out of 255); anything visible is far larger
                diff = ImageChops.difference(a, b).point(lambda v: 255 if v > 8 else 0).getbbox() if a.size == b.size else 'different size'
                check(f'{name} pixel-identical to Docker app', diff is None, f'differs in {diff}')

        browser.close()
finally:
    srv.terminate()

check('no JS errors', not errors, str(errors[:5]))
failed = [r for r in results if not r[1]]
print(f'\n{len(results) - len(failed)}/{len(results)} passed')
sys.exit(1 if failed else 0)
