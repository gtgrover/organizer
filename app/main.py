import asyncio
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

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

class Event(BaseModel):
    day: date
    time: str = Field(default='', max_length=5, pattern=r'^$|^([01]\d|2[0-3]):[0-5]\d$')
    title: str = Field(min_length=1, max_length=160)
    category: str = Field(default='Home', pattern=r'^(Home|Personal|Errands|Other)$')
    description: str = Field(default='', max_length=1000)
    color: str = Field(default='blue', pattern=r'^(blue|red|green|yellow|purple|orange)$')

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

class TaskUpdate(BaseModel):
    done: bool

class Note(BaseModel):
    content: str = Field(max_length=4000)

class View(BaseModel):
    view: str = Field(pattern=r'^(overview|calendar|tasks|notes)$')
    selected_day: date | None = None

def snapshot():
    with connection() as c:
        return {'events':[dict(x) for x in c.execute('SELECT * FROM events ORDER BY day,time,id')],
                'tasks':[dict(x) for x in c.execute('SELECT * FROM tasks ORDER BY id')],
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
    with connection() as c: c.execute('INSERT INTO events(day,time,title,category,description,color) VALUES(?,?,?,?,?,?)',(str(item.day),item.time,item.title.strip(),item.category,item.description.strip(),item.color))
    await broadcast()
    return {'ok':True}

@app.put('/api/events/{item_id}')
async def update_event(item_id:int,item:Event):
    with connection() as c:
        r=c.execute('UPDATE events SET day=?,time=?,title=?,category=?,description=?,color=? WHERE id=?',(str(item.day),item.time,item.title.strip(),item.category,item.description.strip(),item.color,item_id))
        if not r.rowcount: raise HTTPException(404,'Not found')
    await broadcast()
    return {'ok':True}

@app.delete('/api/events/{item_id}')
async def delete_event(item_id:int):
    with connection() as c:
        r=c.execute('DELETE FROM events WHERE id=?',(item_id,))
        if not r.rowcount: raise HTTPException(404,'Not found')
    await broadcast()
    return {'ok':True}

@app.post('/api/tasks')
async def add_task(item:Task):
    with connection() as c: c.execute('INSERT INTO tasks(title) VALUES(?)',(item.title.strip(),))
    await broadcast()
    return {'ok':True}

@app.patch('/api/tasks/{item_id}')
async def update_task(item_id:int,item:TaskUpdate):
    with connection() as c:
        r=c.execute('UPDATE tasks SET done=? WHERE id=?',(int(item.done),item_id))
        if not r.rowcount: raise HTTPException(404,'Not found')
    await broadcast()
    return {'ok':True}

@app.delete('/api/tasks/{item_id}')
async def delete_task(item_id:int):
    with connection() as c:
        r=c.execute('DELETE FROM tasks WHERE id=?',(item_id,))
        if not r.rowcount: raise HTTPException(404,'Not found')
    await broadcast()
    return {'ok':True}

@app.put('/api/notes')
async def update_notes(item:Note):
    with connection() as c: c.execute('UPDATE notes SET content=? WHERE id=1',(item.content,))
    await broadcast()
    return {'ok':True}

@app.put('/api/view')
async def update_view(item:View):
    with connection() as c:
        c.execute("UPDATE settings SET value=? WHERE key='view'",(item.view,))
        if item.selected_day is not None: c.execute("UPDATE settings SET value=? WHERE key='selected_day'",(str(item.selected_day),))
    await broadcast()
    return {'ok':True}

@app.websocket('/ws')
async def websocket(ws:WebSocket):
    await ws.accept()
    clients.add(ws)
    await ws.send_json(snapshot())
    try:
        while True: await ws.receive_text()
    except WebSocketDisconnect: pass
    finally: clients.discard(ws)
