// Local data layer. Replaces the old server API with IndexedDB on this device.
// Everything the pages need goes through the global `DB` object below.
//
// Record shapes (mirrors the original SQLite tables, with UUID ids):
//   events:   {id, day:'YYYY-MM-DD', time:'HH:MM'|'', title, category, description, color, created, updated}
//   tasks:    {id, title, done:boolean, created, updated}
//   notes:    {id:1, content, updated}
//   settings: {key:'view'|'selected_day', value}
//
// Dates are stored as plain 'YYYY-MM-DD' strings in the device's local calendar.
// They are never converted to Date objects for storage, so they cannot shift a
// day when exported/imported in a different timezone. `created`/`updated` are
// real instants (ISO-8601 UTC), used only for sort order and merge decisions.
(function () {
	'use strict';

	const DB_NAME = 'personal-organizer';
	const DB_VERSION = 1;
	const SCHEMA_VERSION = 1; // version of the export/import file format
	const APP_ID = 'personal-organizer';
	const LEGACY_APP_IDS = ['home-organizer']; // backups from the Docker Home Organizer (same format)

	const CATEGORIES = ['Home', 'Personal', 'Errands', 'Other'];
	const COLORS = ['blue', 'red', 'green', 'yellow', 'purple', 'orange'];
	const VIEWS = ['overview', 'calendar', 'tasks', 'notes'];
	const TIME_RE = /^$|^([01]\d|2[0-3]):[0-5]\d$/;
	const LIMITS = { title: 160, description: 1000, notes: 4000 };

	// ---------- helpers ----------

	const now = () => new Date().toISOString();

	function uuid() {
		if (crypto.randomUUID) return crypto.randomUUID();
		// Fallback for plain-http testing on a LAN address (randomUUID needs a secure context).
		const b = crypto.getRandomValues(new Uint8Array(16));
		b[6] = (b[6] & 0x0f) | 0x40;
		b[8] = (b[8] & 0x3f) | 0x80;
		const h = [...b].map(x => x.toString(16).padStart(2, '0')).join('');
		return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
	}

	// True for a real calendar date like 2026-02-28 (rejects 2026-02-30).
	// UTC is used only to check the numbers form a valid date; nothing is stored from it.
	function isDay(value) {
		if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
		const [y, m, d] = value.split('-').map(Number);
		const dt = new Date(Date.UTC(y, m - 1, d));
		return dt.getUTCFullYear() === y && dt.getUTCMonth() === m - 1 && dt.getUTCDate() === d;
	}

	// Same rules as the old server: accepts '14:05', '2:05 PM', '2:05pm', '2 PM', '2pm'.
	function normalizeTime(value) {
		if (value === null || value === undefined) return '';
		if (typeof value !== 'string') return value;
		const text = value.trim().toUpperCase().split(/\s+/).join(' ');
		if (!text) return '';
		const m = /^(\d{1,2})(?::(\d{2}))? ?(AM|PM)$/.exec(text);
		if (m) {
			const hour = Number(m[1]), minute = Number(m[2] || 0);
			if (hour >= 1 && hour <= 12 && minute <= 59) {
				const h24 = (hour % 12) + (m[3] === 'PM' ? 12 : 0);
				return `${String(h24).padStart(2, '0')}:${String(minute).padStart(2, '0')}`;
			}
		}
		return text;
	}

	function timestamp(value, fallback) {
		if (typeof value === 'string' && !Number.isNaN(Date.parse(value))) return new Date(value).toISOString();
		return fallback;
	}

	function cleanEvent(input) {
		const e = input || {};
		const day = String(e.day ?? '');
		if (!isDay(day)) throw new Error('Date must be a valid date.');
		const time = normalizeTime(e.time);
		if (typeof time !== 'string' || !TIME_RE.test(time)) throw new Error('Time must look like 14:30 or 2:30 PM.');
		const title = String(e.title ?? '').trim();
		if (!title) throw new Error('Event title is required.');
		if (title.length > LIMITS.title) throw new Error(`Event title must be ${LIMITS.title} characters or fewer.`);
		const category = e.category ?? 'Home';
		if (!CATEGORIES.includes(category)) throw new Error('Category must be one of: ' + CATEGORIES.join(', ') + '.');
		const description = String(e.description ?? '').trim();
		if (description.length > LIMITS.description) throw new Error(`Description must be ${LIMITS.description} characters or fewer.`);
		const color = e.color ?? 'blue';
		if (!COLORS.includes(color)) throw new Error('Color must be one of: ' + COLORS.join(', ') + '.');
		return { day, time, title, category, description, color };
	}

	function cleanTaskTitle(value) {
		const title = String(value ?? '').trim();
		if (!title) throw new Error('Checklist item is required.');
		if (title.length > LIMITS.title) throw new Error(`Checklist item must be ${LIMITS.title} characters or fewer.`);
		return title;
	}

	function cleanNotes(value) {
		const content = String(value ?? '');
		if (content.length > LIMITS.notes) throw new Error(`Notes must be ${LIMITS.notes} characters or fewer.`);
		return content;
	}

	const byEvent = (a, b) => a.day.localeCompare(b.day) || a.time.localeCompare(b.time) || a.created.localeCompare(b.created) || a.id.localeCompare(b.id);
	const byTask = (a, b) => a.created.localeCompare(b.created) || a.id.localeCompare(b.id);

	// ---------- IndexedDB plumbing ----------

	let dbPromise = null;
	function open() {
		if (!dbPromise) {
			dbPromise = new Promise((resolve, reject) => {
				const req = indexedDB.open(DB_NAME, DB_VERSION);
				req.onupgradeneeded = () => {
					const db = req.result;
					if (!db.objectStoreNames.contains('events')) db.createObjectStore('events', { keyPath: 'id' }).createIndex('day', 'day');
					if (!db.objectStoreNames.contains('tasks')) db.createObjectStore('tasks', { keyPath: 'id' });
					if (!db.objectStoreNames.contains('notes')) db.createObjectStore('notes', { keyPath: 'id' });
					if (!db.objectStoreNames.contains('settings')) db.createObjectStore('settings', { keyPath: 'key' });
				};
				req.onsuccess = () => {
					const db = req.result;
					db.onversionchange = () => { db.close(); location.reload(); };
					resolve(db);
				};
				req.onerror = () => reject(req.error);
				req.onblocked = () => reject(new Error('Close other Personal Organizer tabs and try again.'));
			});
		}
		return dbPromise;
	}

	const request = r => new Promise((resolve, reject) => {
		r.onsuccess = () => resolve(r.result);
		r.onerror = () => reject(r.error);
	});

	// Runs fn(stores) inside one transaction; resolves after it commits.
	async function run(names, mode, fn) {
		const db = await open();
		const tx = db.transaction(names, mode);
		const done = new Promise((resolve, reject) => {
			tx.oncomplete = resolve;
			tx.onerror = () => reject(tx.error);
			tx.onabort = () => reject(tx.error || new Error('Change was not saved.'));
		});
		const stores = Object.fromEntries(names.map(n => [n, tx.objectStore(n)]));
		let result;
		try {
			result = await fn(stores);
		} catch (error) {
			done.catch(() => {});
			try { tx.abort(); } catch (_) { /* already finished */ }
			throw error;
		}
		await done;
		return result;
	}

	// ---------- change notifications (this tab + other tabs on this device) ----------

	const listeners = new Set();
	const channel = 'BroadcastChannel' in self ? new BroadcastChannel('personal-organizer') : null;
	if (channel) channel.onmessage = () => listeners.forEach(fn => fn());
	function changed() {
		listeners.forEach(fn => fn());
		if (channel) channel.postMessage('changed');
	}

	// ---------- public API ----------

	const ALL = ['events', 'tasks', 'notes', 'settings'];

	async function init() {
		await run(['notes', 'settings'], 'readwrite', async s => {
			if (!(await request(s.notes.get(1)))) s.notes.put({ id: 1, content: '', updated: now() });
			if (!(await request(s.settings.get('view')))) s.settings.put({ key: 'view', value: 'overview' });
			if (!(await request(s.settings.get('selected_day')))) s.settings.put({ key: 'selected_day', value: '' });
		});
		// Ask the browser not to evict our data under storage pressure.
		if (navigator.storage && navigator.storage.persist) {
			try { await navigator.storage.persist(); } catch (_) { /* not supported */ }
		}
	}

	// Same shape the old GET /api/state returned.
	async function snapshot() {
		return run(ALL, 'readonly', async s => {
			const [events, tasks, notes, view, selected] = await Promise.all([
				request(s.events.getAll()), request(s.tasks.getAll()), request(s.notes.get(1)),
				request(s.settings.get('view')), request(s.settings.get('selected_day'))
			]);
			return {
				events: events.sort(byEvent),
				tasks: tasks.sort(byTask),
				notes: notes ? notes.content : '',
				view: view ? view.value : 'overview',
				selected_day: selected ? selected.value : ''
			};
		});
	}

	async function addEvent(data) {
		const event = cleanEvent(data), t = now();
		const record = { id: uuid(), ...event, created: t, updated: t };
		await run(['events'], 'readwrite', s => { s.events.add(record); });
		changed();
		return record;
	}

	async function updateEvent(id, data) {
		const event = cleanEvent(data);
		await run(['events'], 'readwrite', async s => {
			const existing = await request(s.events.get(id));
			if (!existing) throw new Error('Not found');
			s.events.put({ ...existing, ...event, updated: now() });
		});
		changed();
	}

	async function deleteEvent(id) {
		await run(['events'], 'readwrite', async s => {
			if (!(await request(s.events.get(id)))) throw new Error('Not found');
			s.events.delete(id);
		});
		changed();
	}

	async function addTask(title) {
		const t = now();
		const record = { id: uuid(), title: cleanTaskTitle(title), done: false, created: t, updated: t };
		await run(['tasks'], 'readwrite', s => { s.tasks.add(record); });
		changed();
		return record;
	}

	async function setTaskDone(id, done) {
		await run(['tasks'], 'readwrite', async s => {
			const existing = await request(s.tasks.get(id));
			if (!existing) throw new Error('Not found');
			s.tasks.put({ ...existing, done: !!done, updated: now() });
		});
		changed();
	}

	async function deleteTask(id) {
		await run(['tasks'], 'readwrite', async s => {
			if (!(await request(s.tasks.get(id)))) throw new Error('Not found');
			s.tasks.delete(id);
		});
		changed();
	}

	async function saveNotes(content) {
		const clean = cleanNotes(content);
		await run(['notes'], 'readwrite', s => { s.notes.put({ id: 1, content: clean, updated: now() }); });
		changed();
	}

	async function setView(view, selectedDay) {
		if (!VIEWS.includes(view)) throw new Error('Unknown view.');
		if (selectedDay !== undefined && selectedDay !== null && !isDay(selectedDay)) throw new Error('Date must be a valid date.');
		await run(['settings'], 'readwrite', s => {
			s.settings.put({ key: 'view', value: view });
			if (selectedDay) s.settings.put({ key: 'selected_day', value: selectedDay });
		});
		changed();
	}

	// ---------- backup: export / import ----------

	async function exportData() {
		const data = await run(ALL, 'readonly', async s => {
			const [events, tasks, notes, settings] = await Promise.all([
				request(s.events.getAll()), request(s.tasks.getAll()), request(s.notes.get(1)), request(s.settings.getAll())
			]);
			return {
				events: events.sort(byEvent),
				tasks: tasks.sort(byTask),
				notes: { content: notes ? notes.content : '', updated: notes ? notes.updated : now() },
				settings: Object.fromEntries(settings.map(x => [x.key, x.value]))
			};
		});
		return { app: APP_ID, schemaVersion: SCHEMA_VERSION, exportedAt: now(), data };
	}

	// Checks a parsed backup file and returns clean records, or throws with a readable message.
	function validateBackup(file) {
		if (!file || typeof file !== 'object' || Array.isArray(file)) throw new Error('This file is not a Personal Organizer backup.');
		if (file.app !== APP_ID && !LEGACY_APP_IDS.includes(file.app)) throw new Error('This file is not a Personal Organizer backup.');
		if (!Number.isInteger(file.schemaVersion) || file.schemaVersion < 1) throw new Error('Backup file has no valid schemaVersion.');
		if (file.schemaVersion > SCHEMA_VERSION) throw new Error('This backup was made by a newer version of the app. Update the app and try again.');
		const d = file.data;
		if (!d || typeof d !== 'object' || !Array.isArray(d.events) || !Array.isArray(d.tasks)) throw new Error('Backup file is missing its events or tasks.');
		const fallback = timestamp(file.exportedAt, now());
		const checkId = (id, what, n, seen) => {
			if (typeof id !== 'string' || !id || id.length > 100) throw new Error(`${what} ${n}: missing or invalid id.`);
			if (seen.has(id)) throw new Error(`${what} ${n}: duplicate id ${id}.`);
			seen.add(id);
		};
		const eventIds = new Set(), taskIds = new Set();
		const events = d.events.map((e, i) => {
			checkId(e && e.id, 'Event', i + 1, eventIds);
			let clean;
			try { clean = cleanEvent(e); } catch (err) { throw new Error(`Event ${i + 1} ("${String(e.title ?? '').slice(0, 40)}"): ${err.message}`); }
			const created = timestamp(e.created, fallback);
			return { id: e.id, ...clean, created, updated: timestamp(e.updated, created) };
		});
		const tasks = d.tasks.map((t, i) => {
			checkId(t && t.id, 'Checklist item', i + 1, taskIds);
			let title;
			try { title = cleanTaskTitle(t.title); } catch (err) { throw new Error(`Checklist item ${i + 1}: ${err.message}`); }
			if (![true, false, 0, 1].includes(t.done ?? false)) throw new Error(`Checklist item ${i + 1}: "done" must be true or false.`);
			const created = timestamp(t.created, fallback);
			return { id: t.id, title, done: !!t.done, created, updated: timestamp(t.updated, created) };
		});
		let notes = null;
		if (d.notes !== undefined && d.notes !== null) {
			const content = typeof d.notes === 'string' ? d.notes : d.notes.content;
			notes = { content: cleanNotes(content), updated: timestamp(d.notes.updated, fallback) };
		}
		const settings = {};
		if (d.settings && VIEWS.includes(d.settings.view)) settings.view = d.settings.view;
		if (d.settings && (d.settings.selected_day === '' || isDay(d.settings.selected_day))) settings.selected_day = d.settings.selected_day;
		return { events, tasks, notes, settings };
	}

	// mode 'replace': wipe this device's data and load the file.
	// mode 'merge': add records whose id is new; for ids already here, keep whichever copy
	//   was edited most recently. Nothing is deleted. Notes from the file are appended
	//   unless they are already present. View settings are left alone.
	async function importData(file, mode) {
		if (mode !== 'replace' && mode !== 'merge') throw new Error('Import mode must be "replace" or "merge".');
		const incoming = validateBackup(file);
		const summary = { mode, added: 0, updated: 0, unchanged: 0, notes: 'unchanged' };
		await run(ALL, 'readwrite', async s => {
			if (mode === 'replace') {
				ALL.forEach(n => s[n].clear());
				incoming.events.forEach(e => s.events.put(e));
				incoming.tasks.forEach(t => s.tasks.put(t));
				s.notes.put({ id: 1, content: incoming.notes ? incoming.notes.content : '', updated: incoming.notes ? incoming.notes.updated : now() });
				s.settings.put({ key: 'view', value: incoming.settings.view || 'overview' });
				s.settings.put({ key: 'selected_day', value: incoming.settings.selected_day ?? '' });
				summary.added = incoming.events.length + incoming.tasks.length;
				summary.notes = incoming.notes && incoming.notes.content ? 'replaced' : 'unchanged';
				return;
			}
			for (const [store, records] of [['events', incoming.events], ['tasks', incoming.tasks]]) {
				for (const record of records) {
					const existing = await request(s[store].get(record.id));
					if (!existing) { s[store].put(record); summary.added++; }
					else if (record.updated > existing.updated) { s[store].put(record); summary.updated++; }
					else summary.unchanged++;
				}
			}
			const theirs = incoming.notes ? incoming.notes.content.trim() : '';
			if (theirs) {
				const local = await request(s.notes.get(1));
				const mine = local ? local.content : '';
				if (!mine.trim()) {
					s.notes.put({ id: 1, content: incoming.notes.content, updated: now() });
					summary.notes = 'added';
				} else if (!mine.includes(theirs)) {
					const combined = mine.replace(/\s+$/, '') + '\n\n' + theirs;
					if (combined.length > LIMITS.notes) summary.notes = 'skipped (too long to combine)';
					else { s.notes.put({ id: 1, content: combined, updated: now() }); summary.notes = 'appended'; }
				}
			}
		});
		changed();
		return summary;
	}

	window.DB = {
		init, snapshot, subscribe: fn => { listeners.add(fn); return () => listeners.delete(fn); },
		addEvent, updateEvent, deleteEvent,
		addTask, setTaskDone, deleteTask,
		saveNotes, setView,
		exportData, importData, validateBackup,
		SCHEMA_VERSION, CATEGORIES, COLORS
	};
})();
