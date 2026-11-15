import asyncio
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError, field_validator

BASE = Path(__file__).resolve().parent
DB = Path(os.environ.get('ORGANIZER_DB', '/app/data/organizer.db'))
DB.parent.mkdir(parents=True, exist_ok=True)
# Changes on every container start, so open pages can tell a redeploy happened.
BUILD_ID = str(int(datetime.now().timestamp()))
app = FastAPI(title='Home Organizer')

@app.middleware('http')
async def no_stale_pages(request, call_next):
    # Make browsers revalidate pages/CSS/JS so a redeploy shows up on reload.
    response = await call_next(request)
    if not request.url.path.startswith('/api'):
        response.headers['Cache-Control'] = 'no-cache'
    return response
app.mount('/static', StaticFiles(directory=BASE / 'static'), name='static')
clients = set()

# ---- backup file format (shared with the offline Personal Organizer in pwa/) ----
APP_ID = 'home-organizer'
COMPATIBLE_APP_IDS = {'home-organizer', 'personal-organizer'}
SCHEMA_VERSION = 1
# Same namespace as tools/export_for_pwa.py, so existing rows get the same ids there and here.
UID_NAMESPACE = uuid.UUID('6f1d2c1e-5b8a-4c35-9a0e-3d1f2b7c9e44')
LEGACY_BASE = datetime(2020, 1, 1, tzinfo=timezone.utc)
VIEWS = {'overview', 'calendar', 'tasks', 'notes'}


def iso(dt: datetime) -> str:
    """UTC timestamp in exactly the form JavaScript's toISOString() produces, so the two apps compare equal."""
    dt = dt.astimezone(timezone.utc)
    return dt.strftime('%Y-%m-%dT%H:%M:%S.') + f'{dt.microsecond // 1000:03d}Z'


def now() -> str:
    return iso(datetime.now(timezone.utc))


@contextmanager
def connection():
    c = sqlite3.connect(DB, timeout=10)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()

with connection() as c:
    c.executescript('''
    CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, day TEXT NOT NULL, time TEXT NOT NULL DEFAULT '', title TEXT NOT NULL, category TEXT NOT NULL DEFAULT 'Home', description TEXT NOT NULL DEFAULT '', color TEXT NOT NULL DEFAULT 'blue');
    CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY, title TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY CHECK(id=1), content TEXT NOT NULL DEFAULT '');
    CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    INSERT OR IGNORE INTO notes(id,content) VALUES(1,'');
    INSERT OR IGNORE INTO settings(key,value) VALUES('view','overview');
    INSERT OR IGNORE INTO settings(key,value) VALUES('selected_day','');
    ''')
    event_columns = {row['name'] for row in c.execute('PRAGMA table_info(events)')}
    if 'description' not in event_columns:
        c.execute("ALTER TABLE events ADD COLUMN description TEXT NOT NULL DEFAULT ''")
    if 'color' not in event_columns:
        c.execute("ALTER TABLE events ADD COLUMN color TEXT NOT NULL DEFAULT 'blue'")
    # Stable ids + timestamps, so backups can be merged with no duplicates.
    # Existing rows get a fixed uid (uuid5 of "event:<id>") and an old placeholder time
    # spread by id, which keeps the original order and loses to any real later edit.
    for table, kind in (('events', 'event'), ('tasks', 'task')):
        columns = {row['name'] for row in c.execute(f'PRAGMA table_info({table})')}
        for column in ('uid', 'created', 'updated'):
            if column not in columns:
                c.execute(f'ALTER TABLE {table} ADD COLUMN {column} TEXT')
        for row in c.execute(f'SELECT id FROM {table} WHERE uid IS NULL OR created IS NULL OR updated IS NULL').fetchall():
            stamp = iso(LEGACY_BASE + timedelta(milliseconds=row['id']))
            c.execute(f'UPDATE {table} SET uid=COALESCE(uid,?), created=COALESCE(created,?), updated=COALESCE(updated,?) WHERE id=?',
                      (str(uuid.uuid5(UID_NAMESPACE, f"{kind}:{row['id']}")), stamp, stamp, row['id']))
        c.execute(f'CREATE UNIQUE INDEX IF NOT EXISTS {table}_uid ON {table}(uid)')
    if 'updated' not in {row['name'] for row in c.execute('PRAGMA table_info(notes)')}:
        c.execute('ALTER TABLE notes ADD COLUMN updated TEXT')
        c.execute('UPDATE notes SET updated=? WHERE id=1', (iso(LEGACY_BASE),))

class Event(BaseModel):
    day: date
    time: str = Field(default='', max_length=5, pattern=r'^$|^([01]\d|2[0-3]):[0-5]\d$')
    title: str = Field(min_length=1, max_length=160)
    category: str = Field(default='Home', pattern=r'^(Home|Personal|Errands|Other)$')
    description: str = Field(default='', max_length=1000)
    color: str = Field(default='blue', pattern=r'^(blue|red|green|yellow|purple|orange)$')

    @field_validator('title', 'description', mode='before')
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator('time', mode='before')
    @classmethod
    def normalize_time(cls, value):
        if value is None:
            return ''
        if not isinstance(value, str):
            return value
        normalized = ' '.join(value.strip().upper().split())
        if not normalized:
            return ''
        for time_format in ('%I:%M %p', '%I:%M%p', '%I %p', '%I%p'):
            try:
                return datetime.strptime(normalized, time_format).strftime('%H:%M')
            except ValueError:
                continue
        return normalized

class Task(BaseModel):
    title: str = Field(min_length=1, max_length=160)

    @field_validator('title', mode='before')
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value

class TaskUpdate(BaseModel):
    done: bool

class Note(BaseModel):
    content: str = Field(max_length=4000)

class View(BaseModel):
    view: str = Field(pattern=r'^(overview|calendar|tasks|notes)$')
    selected_day: date | None = None

class ImportRequest(BaseModel):
    mode: Literal['merge', 'replace']
    backup: Any

EVENT_FIELDS = 'uid AS id, day, time, title, category, description, color, created, updated'
TASK_FIELDS = 'uid AS id, title, done, created, updated'

def read_events(c):
    return [dict(x) for x in c.execute(f'SELECT {EVENT_FIELDS} FROM events ORDER BY day, time, created, uid')]

def read_tasks(c):
    return [{**dict(x), 'done': bool(x['done'])} for x in c.execute(f'SELECT {TASK_FIELDS} FROM tasks ORDER BY created, uid')]

def snapshot():
    with connection() as c:
        return {'events': read_events(c),
                'tasks': read_tasks(c),
                'notes':c.execute('SELECT content FROM notes WHERE id=1').fetchone()['content'],
                'view':c.execute("SELECT value FROM settings WHERE key='view'").fetchone()['value'],
                'selected_day':c.execute("SELECT value FROM settings WHERE key='selected_day'").fetchone()['value'],
                'build':BUILD_ID}

async def broadcast():
    payload = json.dumps(snapshot())
    for ws in list(clients):
        try: await ws.send_text(payload)
        except Exception: clients.discard(ws)

@app.get('/')
def root(): return FileResponse(BASE / 'static' / 'display.html')
@app.get('/display')
def display(): return FileResponse(BASE / 'static' / 'display.html')
@app.get('/control')
def control(): return FileResponse(BASE / 'static' / 'control.html')
@app.get('/admin')
def admin(): return FileResponse(BASE / 'static' / 'admin.html')
@app.get('/api/state')
def state(): return snapshot()

@app.post('/api/events')
async def add_event(item:Event):
    t = now()
    with connection() as c: c.execute('INSERT INTO events(uid,day,time,title,category,description,color,created,updated) VALUES(?,?,?,?,?,?,?,?,?)',(str(uuid.uuid4()),str(item.day),item.time,item.title,item.category,item.description,item.color,t,t))
    await broadcast()
    return {'ok':True}

@app.put('/api/events/{item_id}')
async def update_event(item_id:str,item:Event):
    with connection() as c:
        r=c.execute('UPDATE events SET day=?,time=?,title=?,category=?,description=?,color=?,updated=? WHERE uid=?',(str(item.day),item.time,item.title,item.category,item.description,item.color,now(),item_id))
        if not r.rowcount: raise HTTPException(404,'Not found')
    await broadcast()
    return {'ok':True}

@app.delete('/api/events/{item_id}')
async def delete_event(item_id:str):
    with connection() as c:
        r=c.execute('DELETE FROM events WHERE uid=?',(item_id,))
        if not r.rowcount: raise HTTPException(404,'Not found')
    await broadcast()
    return {'ok':True}

@app.post('/api/tasks')
async def add_task(item:Task):
    t = now()
    with connection() as c: c.execute('INSERT INTO tasks(uid,title,done,created,updated) VALUES(?,?,0,?,?)',(str(uuid.uuid4()),item.title,t,t))
    await broadcast()
    return {'ok':True}

@app.patch('/api/tasks/{item_id}')
async def update_task(item_id:str,item:TaskUpdate):
    with connection() as c:
        r=c.execute('UPDATE tasks SET done=?,updated=? WHERE uid=?',(int(item.done),now(),item_id))
        if not r.rowcount: raise HTTPException(404,'Not found')
    await broadcast()
    return {'ok':True}

@app.delete('/api/tasks/{item_id}')
async def delete_task(item_id:str):
    with connection() as c:
        r=c.execute('DELETE FROM tasks WHERE uid=?',(item_id,))
        if not r.rowcount: raise HTTPException(404,'Not found')
    await broadcast()
    return {'ok':True}

@app.put('/api/notes')
async def update_notes(item:Note):
    with connection() as c: c.execute('UPDATE notes SET content=?,updated=? WHERE id=1',(item.content,now()))
    await broadcast()
    return {'ok':True}

@app.put('/api/view')
async def update_view(item:View):
    with connection() as c:
        c.execute("UPDATE settings SET value=? WHERE key='view'",(item.view,))
        if item.selected_day is not None: c.execute("UPDATE settings SET value=? WHERE key='selected_day'",(str(item.selected_day),))
    await broadcast()
    return {'ok':True}

# ---- backup: export / import (same file format and rules as pwa/db.js) ----

@app.get('/api/export')
def export_data():
    with connection() as c:
        notes = c.execute('SELECT content, updated FROM notes WHERE id=1').fetchone()
        settings = {r['key']: r['value'] for r in c.execute('SELECT key, value FROM settings')}
        return {'app': APP_ID, 'schemaVersion': SCHEMA_VERSION, 'exportedAt': now(),
                'data': {'events': read_events(c), 'tasks': read_tasks(c),
                         'notes': {'content': notes['content'], 'updated': notes['updated'] or now()},
                         'settings': {'view': settings.get('view', 'overview'), 'selected_day': settings.get('selected_day', '')}}}


def _timestamp(value, fallback):
    if isinstance(value, str):
        try:
            return iso(datetime.fromisoformat(value.replace('Z', '+00:00')))
        except ValueError:
            pass
    return fallback


def _first_error(exc: ValidationError):
    err = exc.errors()[0]
    field = '.'.join(str(p) for p in err['loc'])
    return f'{field}: {err["msg"]}' if field else err['msg']


def validate_backup(file):
    """Checks a parsed backup file; returns clean records or raises ValueError with a readable message."""
    if not isinstance(file, dict) or file.get('app') not in COMPATIBLE_APP_IDS:
        raise ValueError('This file is not an organizer backup.')
    version = file.get('schemaVersion')
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise ValueError('Backup file has no valid schemaVersion.')
    if version > SCHEMA_VERSION:
        raise ValueError('This backup was made by a newer version of the app. Update the app and try again.')
    d = file.get('data')
    if not isinstance(d, dict) or not isinstance(d.get('events'), list) or not isinstance(d.get('tasks'), list):
        raise ValueError('Backup file is missing its events or tasks.')
    fallback = _timestamp(file.get('exportedAt'), now())

    def check_id(record, what, n, seen):
        rid = record.get('id') if isinstance(record, dict) else None
        if not isinstance(rid, str) or not rid or len(rid) > 100:
            raise ValueError(f'{what} {n}: missing or invalid id.')
        if rid in seen:
            raise ValueError(f'{what} {n}: duplicate id {rid}.')
        seen.add(rid)
        return rid

    events, seen = [], set()
    for n, e in enumerate(d['events'], 1):
        rid = check_id(e, 'Event', n, seen)
        try:
            ev = Event(**{k: e[k] for k in ('day', 'time', 'title', 'category', 'description', 'color') if k in e and e[k] is not None})
        except ValidationError as exc:
            raise ValueError(f'Event {n} ("{str(e.get("title", ""))[:40]}"): {_first_error(exc)}')
        created = _timestamp(e.get('created'), fallback)
        events.append({'id': rid, 'day': str(ev.day), 'time': ev.time, 'title': ev.title, 'category': ev.category,
                       'description': ev.description, 'color': ev.color, 'created': created,
                       'updated': _timestamp(e.get('updated'), created)})
    tasks, seen = [], set()
    for n, t in enumerate(d['tasks'], 1):
        rid = check_id(t, 'Checklist item', n, seen)
        try:
            title = Task(title=t.get('title')).title
        except ValidationError as exc:
            raise ValueError(f'Checklist item {n}: {_first_error(exc)}')
        done = t.get('done', False)
        if done not in (True, False, 0, 1):
            raise ValueError(f'Checklist item {n}: "done" must be true or false.')
        created = _timestamp(t.get('created'), fallback)
        tasks.append({'id': rid, 'title': title, 'done': bool(done), 'created': created,
                      'updated': _timestamp(t.get('updated'), created)})
    notes = None
    raw = d.get('notes')
    if raw is not None:
        content = raw if isinstance(raw, str) else (raw.get('content') if isinstance(raw, dict) else None)
        if not isinstance(content, str) or len(content) > 4000:
            raise ValueError('Notes must be text of 4000 characters or fewer.')
        notes = {'content': content, 'updated': _timestamp(raw.get('updated') if isinstance(raw, dict) else None, fallback)}
    settings, s = {}, d.get('settings') or {}
    if isinstance(s, dict):
        if s.get('view') in VIEWS:
            settings['view'] = s['view']
        sd = s.get('selected_day')
        if sd == '':
            settings['selected_day'] = ''
        elif isinstance(sd, str):
            try:
                date.fromisoformat(sd); settings['selected_day'] = sd
            except ValueError:
                pass
    return {'events': events, 'tasks': tasks, 'notes': notes, 'settings': settings}


def _put_event(c, e):
    c.execute('''INSERT INTO events(uid,day,time,title,category,description,color,created,updated) VALUES(?,?,?,?,?,?,?,?,?)
                 ON CONFLICT(uid) DO UPDATE SET day=excluded.day, time=excluded.time, title=excluded.title, category=excluded.category,
                 description=excluded.description, color=excluded.color, created=excluded.created, updated=excluded.updated''',
              (e['id'], e['day'], e['time'], e['title'], e['category'], e['description'], e['color'], e['created'], e['updated']))


def _put_task(c, t):
    c.execute('''INSERT INTO tasks(uid,title,done,created,updated) VALUES(?,?,?,?,?)
                 ON CONFLICT(uid) DO UPDATE SET title=excluded.title, done=excluded.done, created=excluded.created, updated=excluded.updated''',
              (t['id'], t['title'], int(t['done']), t['created'], t['updated']))


@app.post('/api/import')
async def import_data(item: ImportRequest):
    """replace: wipe and load the file. merge: add new ids; for ids already here keep the most
    recently edited copy; never delete; append the file's notes unless already present."""
    try:
        incoming = validate_backup(item.backup)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    summary = {'mode': item.mode, 'added': 0, 'updated': 0, 'unchanged': 0, 'notes': 'unchanged'}
    with connection() as c:
        if item.mode == 'replace':
            c.execute('DELETE FROM events'); c.execute('DELETE FROM tasks')
            for e in incoming['events']: _put_event(c, e)
            for t in incoming['tasks']: _put_task(c, t)
            n = incoming['notes']
            c.execute('UPDATE notes SET content=?, updated=? WHERE id=1', (n['content'] if n else '', n['updated'] if n else now()))
            c.execute("UPDATE settings SET value=? WHERE key='view'", (incoming['settings'].get('view', 'overview'),))
            c.execute("UPDATE settings SET value=? WHERE key='selected_day'", (incoming['settings'].get('selected_day', ''),))
            summary['added'] = len(incoming['events']) + len(incoming['tasks'])
            summary['notes'] = 'replaced' if n and n['content'] else 'unchanged'
        else:
            for table, records, put in (('events', incoming['events'], _put_event), ('tasks', incoming['tasks'], _put_task)):
                for r in records:
                    existing = c.execute(f'SELECT updated FROM {table} WHERE uid=?', (r['id'],)).fetchone()
                    if not existing:
                        put(c, r); summary['added'] += 1
                    elif r['updated'] > (existing['updated'] or ''):
                        put(c, r); summary['updated'] += 1
                    else:
                        summary['unchanged'] += 1
            theirs = incoming['notes']['content'].strip() if incoming['notes'] else ''
            if theirs:
                mine = c.execute('SELECT content FROM notes WHERE id=1').fetchone()['content']
                if not mine.strip():
                    c.execute('UPDATE notes SET content=?, updated=? WHERE id=1', (incoming['notes']['content'], now())); summary['notes'] = 'added'
                elif theirs not in mine:
                    combined = mine.rstrip() + '\n\n' + theirs
                    if len(combined) > 4000:
                        summary['notes'] = 'skipped (too long to combine)'
                    else:
                        c.execute('UPDATE notes SET content=?, updated=? WHERE id=1', (combined, now())); summary['notes'] = 'appended'
    await broadcast()
    return summary

@app.websocket('/ws')
async def websocket(ws:WebSocket):
    await ws.accept()
    clients.add(ws)
    await ws.send_json(snapshot())
    try:
        while True: await ws.receive_text()
    except WebSocketDisconnect: pass
    finally: clients.discard(ws)
