#!/usr/bin/env python3
"""Copy the shared screens in shared/ into both apps.

    python3 tools/sync.py           # write app/static/ and pwa/
    python3 tools/sync.py --check   # only report files that are out of date (exit 1 if any)

shared/ is the single place to edit the look and behaviour of the screens. The
generated copies in app/static/ (Docker "Home Organizer") and pwa/ (offline
"Personal Organizer") start with a GENERATED notice. Edit shared/, not them.

The settings below are the only differences between the two apps. Each app also has
its own data layer, which is NOT generated:
    app/static/db-server.js   talks to the household server
    pwa/db.js                 stores data on the device (IndexedDB)
Both provide the same DB.* functions, so the shared screens work with either.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHARED = ROOT / 'shared'

PWA_HEAD = '\n\t'.join([
    '<link rel="manifest" href="manifest.webmanifest">',
    '<meta name="theme-color" content="#101724">',
    '<meta name="apple-mobile-web-app-capable" content="yes">',
    '<meta name="mobile-web-app-capable" content="yes">',
    '<meta name="apple-mobile-web-app-status-bar-style" content="black">',
    '<meta name="apple-mobile-web-app-title" content="Organizer">',
    '<link rel="icon" href="icons/icon-192.png">',
    '<link rel="apple-touch-icon" href="icons/apple-touch-icon.png">',
])

APPS = {
    'app/static': {
        'pages': {'display.html': 'display.html', 'control.html': 'control.html'},
        'APP_SLUG': 'home-organizer',
        'APP_NAME': 'Home Organizer',
        'DISPLAY_TITLE': 'Home Organizer Display',
        'CONTROL_TITLE': 'Home Organizer Controller',
        'CONTROL_SUBTITLE': 'Shared household organizer',
        'ASSETS': '/static/',
        'DISPLAY_HREF': '/display',
        'CONTROL_HREF': '/control',
        'HEAD_EXTRA': '',
        'DATA_SCRIPT': 'db-server.js',
        'APP_SCRIPTS': '',
        'BACKUP_NOTE': 'Download everything in this organizer as a file, or load one back in. Backup files also work with the offline Personal Organizer app.',
    },
    'pwa': {
        'pages': {'display.html': 'index.html', 'control.html': 'control.html'},
        'APP_SLUG': 'personal-organizer',
        'APP_NAME': 'Personal Organizer',
        'DISPLAY_TITLE': 'Personal Organizer',
        'CONTROL_TITLE': 'Personal Organizer Controller',
        'CONTROL_SUBTITLE': 'Personal organizer',
        'ASSETS': '',
        'DISPLAY_HREF': 'index.html',
        'CONTROL_HREF': 'control.html',
        'HEAD_EXTRA': PWA_HEAD,
        'DATA_SCRIPT': 'db.js',
        'APP_SCRIPTS': '<script src="pwa.js"></script>',
        'BACKUP_NOTE': 'Your data is stored only on this device. Export a backup now and then, or to share your calendar with someone else.',
    },
}

# Copied unchanged into every app.
SHARED_FILES = ['common.css', 'common.js', 'backup.js', 'print.css']


def notice(name, source):
    text = f'GENERATED from shared/{source} by tools/sync.py. Edit that file, not this one.'
    return f'<!-- {text} -->' if name.endswith('.html') else f'/* {text} */'


def render(source, settings):
    text = (SHARED / source).read_text()
    for key, value in settings.items():
        if key != 'pages':
            text = text.replace('{{' + key + '}}', value)
    if '{{' in text:
        raise SystemExit(f'shared/{source}: unfilled placeholder near {text[text.index("{{"):][:40]!r}')
    return text


def outputs():
    for app, settings in APPS.items():
        for source, target in settings['pages'].items():
            body = render(source, settings)
            # notice goes after <!doctype html> so pages stay in standards mode
            if body.lower().startswith('<!doctype html>'):
                head, tail = body[:15], body[15:]
                yield ROOT / app / target, head + notice(target, source) + tail
            else:
                yield ROOT / app / target, notice(target, source) + '\n' + body
        for name in SHARED_FILES:
            yield ROOT / app / name, notice(name, name) + '\n' + (SHARED / name).read_text()


def main():
    check = '--check' in sys.argv
    stale = []
    for path, text in outputs():
        current = path.read_text() if path.exists() else None
        if current == text:
            continue
        stale.append(path.relative_to(ROOT))
        if not check:
            path.write_text(text)
    if check:
        if stale:
            print('Out of date (run: python3 tools/sync.py):')
            for p in stale:
                print('  ', p)
            sys.exit(1)
        print('Both apps are in sync with shared/.')
    else:
        print(f'Updated {len(stale)} file(s)' + (':' if stale else '.'))
        for p in stale:
            print('  ', p)
        if any(str(p).startswith('pwa/') for p in stale):
            print('\nOffline app changed: bump VERSION in pwa/sw.js before deploying it.')
        if any(str(p).startswith('app/') for p in stale):
            print('\nDocker app changed: rebuild it with  docker compose up --build -d')


if __name__ == '__main__':
    main()
