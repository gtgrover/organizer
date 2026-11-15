// Offline support: registers the service worker and applies app updates.
(function () {
	'use strict';
	if (!('serviceWorker' in navigator)) return;

	// Only reload for an *update* — not when the service worker takes control the first time.
	const hadController = !!navigator.serviceWorker.controller;
	let reloading = false;

	function reloadWhenIdle() {
		if (reloading) return;
		// Don't throw away something half-typed on the control page; try again shortly.
		if (typeof window.hasUnsavedInput === 'function' && window.hasUnsavedInput()) {
			setTimeout(reloadWhenIdle, 5000);
			return;
		}
		reloading = true;
		location.reload();
	}

	navigator.serviceWorker.addEventListener('controllerchange', () => {
		if (hadController) reloadWhenIdle();
	});

	window.addEventListener('load', () => {
		navigator.serviceWorker.register('sw.js').then(reg => {
			// A wall display may stay open for days: look for a new version every hour.
			setInterval(() => reg.update().catch(() => {}), 60 * 60 * 1000);
		}).catch(error => console.warn('Offline support unavailable:', error));
	});
})();
