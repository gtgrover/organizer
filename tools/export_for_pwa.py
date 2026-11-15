#!/usr/bin/env python3
"""One-off: convert the Docker app's SQLite database into a PWA backup file.

Usage:
    python3 tools/export_for_pwa.py [path/to/organizer.db] [output.json]

Defaults: data/organizer.db -> personal-organizer-migrated.json
Then in the PWA: Control -> Backup -> choose the file -> Import (Replace or Merge).

Only reads the database (opened read-only), so it is safe to run while the
container is up. Standard library only.

IDs: each old integer id becomes a fixed UUID (uuid5 of "event:<id>" / "task:<id>"),
so running this twice and importing both files with Merge does not create duplicates.
"""
import json
import re
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta, timezone

SCHEMA_VERSION = 1
NAMESPACE = uuid.UUID('6f1d2c1e-5b8a-4c35-9a0e-3d1f2b7c9e44')  # fixed: keeps ids stable between runs
CATEGORIES = {'Home', 'Personal', 'Errands', 'Other'}
COLORS = {'blue', 'red', 'green', 'yellow', 'purple', 'orange'}
VIEWS = {'overview', 'calendar', 'tasks', 'notes'}


def iso(dt):
    return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.') + f'{dt.microsecond // 1000:03d}Z'


def normalize_time(value):
    text = ' '.join((value or '').strip().upper().split())
    if not text or re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d', text):
        return text
    for fmt in ('%I:%M %p', '%I:%M%p', '%I %p', '%I%p'):
        try:
            return datetime.strptime(text, fmt).strftime('%H:%M')
        except ValueError:
            pass
    raise ValueError(f'unrecognised time {value!r}')


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else 'data/organizer.db'
    dst = sys.argv[2] if len(sys.argv) > 2 else 'personal-organizer-migrated.json'
    db = sqlite3.connect(f'file:{src}?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    columns = {r['name'] for r in db.execute('PRAGMA table_info(events)')}

    stamp = iso(datetime.now(timezone.utc))
    # The old database has no timestamps. Use a fixed, old placeholder spread by id:
    # - keeps the old sort order (by id) in the PWA
    # - is the same on every run, and older than any edit made in the PWA, so
    #   re-importing this file with Merge never overwrites newer changes.
    base = datetime(2020, 1, 1, tzinfo=timezone.utc)
    created = lambda n: iso(base + timedelta(milliseconds=n))

    events, problems = [], []
    for r in db.execute('SELECT * FROM events ORDER BY id'):
        try:
            day = r['day']
            datetime.strptime(day, '%Y-%m-%d')  # must be a real date; kept as the same text
            event = {
                'id': str(uuid.uuid5(NAMESPACE, f"event:{r['id']}")),
                'day': day,
                'time': normalize_time(r['time']),
                'title': (r['title'] or '').strip()[:160],
                'category': r['category'] if r['category'] in CATEGORIES else 'Other',
                'description': ((r['description'] if 'description' in columns else '') or '').strip()[:1000],
                'color': r['color'] if 'color' in columns and r['color'] in COLORS else 'blue',
                'created': created(r['id']),
                'updated': created(r['id']),
            }
            if not event['title']:
                raise ValueError('empty title')
            events.append(event)
        except Exception as exc:  # report and skip, rather than lose everything
            problems.append(f"event id {r['id']}: {exc}")

    tasks = [{
        'id': str(uuid.uuid5(NAMESPACE, f"task:{r['id']}")),
        'title': r['title'].strip()[:160],
        'done': bool(r['done']),
        'created': created(r['id']),
        'updated': created(r['id']),
    } for r in db.execute('SELECT * FROM tasks ORDER BY id') if (r['title'] or '').strip()]

    note = db.execute('SELECT content FROM notes WHERE id=1').fetchone()
    settings = {r['key']: r['value'] for r in db.execute('SELECT key, value FROM settings')}
    out = {
        'app': 'personal-organizer',
        'schemaVersion': SCHEMA_VERSION,
        'exportedAt': stamp,
        'data': {
            'events': events,
            'tasks': tasks,
            'notes': {'content': (note['content'] if note else '')[:4000], 'updated': stamp},
            'settings': {
                'view': settings.get('view') if settings.get('view') in VIEWS else 'overview',
                'selected_day': settings.get('selected_day', ''),
            },
        },
    }
    with open(dst, 'w', encoding='utf-8') as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f'Wrote {dst}: {len(events)} events, {len(tasks)} tasks, notes {len(out["data"]["notes"]["content"])} chars.')
    for p in problems:
        print('  skipped', p, file=sys.stderr)


if __name__ == '__main__':
    main()
