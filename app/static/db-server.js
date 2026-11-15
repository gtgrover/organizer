// Data layer for the Docker app: the same DB.* functions as pwa/db.js, but backed by
// the household server, so every screen on the network shares one set of data.
// The shared screens (shared/*) call only these functions, so they work with either app.
(function () {
	'use strict';

	async function call(path, method = 'GET', body) {
		const r = await fetch('/api/' + path, {
			method,
			headers: { 'Content-Type': 'application/json' },
			body: body === undefined ? undefined : JSON.stringify(body)
		});
		if (!r.ok) {
			let message = await r.text();
			try {
				const j = JSON.parse(message);
				message = Array.isArray(j.detail) ? j.detail.map(d => d.msg).join('; ') : (j.detail || message);
			} catch (_) { /* not JSON */ }
			throw new Error(message);
		}
		return r.json();
	}

	// After a redeploy the server reports a new build id; reload to pick up the new
	// pages (waiting while something is half-typed on the control page).
	let loadedBuild = null;
	function reloadWhenIdle() {
		if (typeof window.hasUnsavedInput === 'function' && window.hasUnsavedInput()) setTimeout(reloadWhenIdle, 5000);
		else location.reload();
	}
	function checkBuild(s) {
		if (!s.build) return;
		if (loadedBuild === null) loadedBuild = s.build;
		else if (s.build !== loadedBuild) reloadWhenIdle();
	}

	async function snapshot() {
		const s = await call('state');
		checkBuild(s);
		return s;
	}

	// Live updates: the server pushes a message over a WebSocket after every change
	// from any device; if the connection drops, poll every 2.5s until it's back.
	const listeners = new Set();
	const notify = () => listeners.forEach(fn => fn());
	let live = false;
	function startLive() {
		let poll = null;
		const startPolling = () => { if (!poll) poll = setInterval(notify, 2500); };
		function open() {
			const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
			ws.onopen = () => { if (poll) { clearInterval(poll); poll = null; } };
			ws.onmessage = notify;
			ws.onclose = () => { startPolling(); setTimeout(open, 1500); };
		}
		open();
	}
	function subscribe(fn) {
		listeners.add(fn);
		if (!live) { live = true; startLive(); }
		return () => listeners.delete(fn);
	}

	window.DB = {
		init: () => Promise.resolve(),
		snapshot,
		subscribe,
		addEvent: data => call('events', 'POST', data),
		updateEvent: (id, data) => call('events/' + encodeURIComponent(id), 'PUT', data),
		deleteEvent: id => call('events/' + encodeURIComponent(id), 'DELETE'),
		addTask: title => call('tasks', 'POST', { title }),
		setTaskDone: (id, done) => call('tasks/' + encodeURIComponent(id), 'PATCH', { done: !!done }),
		deleteTask: id => call('tasks/' + encodeURIComponent(id), 'DELETE'),
		saveNotes: content => call('notes', 'PUT', { content }),
		setView: (view, selectedDay) => call('view', 'PUT', { view, selected_day: selectedDay || undefined }),
		exportData: () => call('export'),
		importData: (file, mode) => call('import', 'POST', { mode, backup: file })
	};
})();
