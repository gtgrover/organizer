// Backup files: export this device's data to a file, and import one back in.
(function () {
	'use strict';

	// Local date + time for file names, e.g. 2026-11-15-0930 (device clock, not UTC).
	function stamp() {
		const d = new Date(), p = n => String(n).padStart(2, '0');
		return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}`;
	}

	// Export formats. Each one takes the object from DB.exportData() and returns
	// {filename, mime, text}.
	//
	// Hook for a future calendar export: add an `ics` entry that turns
	// snapshot.data.events into iCalendar VEVENTs (all-day events use
	// DTSTART;VALUE=DATE:YYYYMMDD straight from `day`; timed events use a floating
	// local DTSTART:YYYYMMDDTHHMM00 so they stay in the user's timezone), then add a
	// button that calls Backup.exportAs('ics').
	const slug = document.documentElement.dataset.app || 'organizer'; // set per app by tools/sync.py
	const EXPORTERS = {
		json: snapshot => ({
			filename: `${slug}-backup-${stamp()}.json`,
			mime: 'application/json',
			text: JSON.stringify(snapshot, null, 2)
		})
	};

	const isMobile = () => /iPhone|iPad|iPod|Android/.test(navigator.userAgent) ||
		(navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1); // iPadOS reports as a Mac

	// Phones/tablets: open the share sheet ("Save to Files", AirDrop, Mail...).
	// Desktop, or if sharing isn't available: a normal file download.
	async function save({ filename, mime, text }) {
		const blob = new Blob([text], { type: mime });
		if (isMobile() && navigator.canShare) {
			const file = new File([blob], filename, { type: mime });
			if (navigator.canShare({ files: [file] })) {
				await navigator.share({ files: [file], title: filename });
				return filename;
			}
		}
		const url = URL.createObjectURL(blob);
		const a = Object.assign(document.createElement('a'), { href: url, download: filename });
		document.body.appendChild(a);
		a.click();
		a.remove();
		setTimeout(() => URL.revokeObjectURL(url), 10000);
		return filename;
	}

	async function exportAs(format) {
		const exporter = EXPORTERS[format];
		if (!exporter) throw new Error('Unknown export format: ' + format);
		return save(exporter(await DB.exportData()));
	}

	async function importFile(file, mode) {
		let parsed;
		try { parsed = JSON.parse(await file.text()); } catch (_) { throw new Error('That file is not valid JSON.'); }
		return DB.importData(parsed, mode);
	}

	function describe(r) {
		const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;
		if (r.mode === 'replace') return `Replaced all data: ${plural(r.added, 'item')} loaded${r.notes === 'replaced' ? ', notes restored' : ''}.`;
		return `Merged: ${plural(r.added, 'new item')}, ${r.updated} updated, ${r.unchanged} already up to date. Notes: ${r.notes}.`;
	}

	window.Backup = { exportAs, importFile, describe, EXPORTERS };
})();
