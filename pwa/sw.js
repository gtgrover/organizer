// Service worker: keeps a copy of every app file on the device so the app runs offline.
//
// TO SHIP AN UPDATE: change VERSION below (any new value) and deploy.
// Browsers notice the changed sw.js, download the new files, and open pages reload
// themselves. If VERSION is not changed, users keep the old cached files.
const VERSION = '2026-11-15.2';
const CACHE = 'personal-organizer-' + VERSION;

const FILES = [
	'./',
	'index.html',
	'control.html',
	'common.css',
	'print.css',
	'db.js',
	'common.js',
	'backup.js',
	'pwa.js',
	'manifest.webmanifest',
	'icons/icon-192.png',
	'icons/icon-512.png',
	'icons/icon-maskable-512.png',
	'icons/apple-touch-icon.png'
];

self.addEventListener('install', event => {
	event.waitUntil(
		caches.open(CACHE)
			// cache:'reload' skips the browser's HTTP cache so we store the freshly deployed files.
			.then(cache => cache.addAll(FILES.map(f => new Request(f, { cache: 'reload' }))))
			.then(() => self.skipWaiting())
	);
});

self.addEventListener('activate', event => {
	event.waitUntil(
		caches.keys()
			.then(keys => Promise.all(keys.filter(k => k.startsWith('personal-organizer-') && k !== CACHE).map(k => caches.delete(k))))
			.then(() => self.clients.claim())
	);
});

// Cache-first: answer from the stored copy; only fall back to the network for
// anything not in the cache. The app itself makes no network requests.
self.addEventListener('fetch', event => {
	const req = event.request;
	if (req.method !== 'GET' || new URL(req.url).origin !== self.location.origin) return;
	event.respondWith((async () => {
		const cache = await caches.open(CACHE);
		const hit = await cache.match(req, { ignoreSearch: true });
		if (hit) return hit;
		if (req.mode === 'navigate') {
			try { return await fetch(req); } catch (_) { return (await cache.match('index.html')) || Response.error(); }
		}
		return fetch(req);
	})());
});
