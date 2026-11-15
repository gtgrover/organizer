# What changed from the Docker app

**Not carried over**

- **Controlling another screen.** In the Docker app, a phone could change what a
  separate wall screen showed. Now the *Control wall display* buttons only affect the
  display page **on the same device**. Two tabs or windows on one device still stay in
  sync with each other instantly.
- **One shared household database.** Each device now has its own data. The only way
  to share is to export a backup and import it on another device with Merge.
- **The `/admin` page.** It was an empty placeholder, so it was dropped.
- **Server-side checks.** These now run in the browser (`db.js`) with the same limits
  as before: title 160 characters, description 1000, notes 4000, the same categories
  and colours, real dates only, and times like "2 PM" accepted.

**Changed**

- **Printing** now happens on the same page instead of in a pop-up window, because
  pop-ups don't work reliably in home-screen apps. The printed page is the same as
  before: it was pixel-identical in testing.
- **Record ids** are UUIDs instead of 1, 2, 3…, so backups from different people can
  be merged without clashes. Each record also has `created` and `updated` times, used
  for sort order and for merging.
- **Page addresses:** `/display` is now `index.html` (the start page) and `/control`
  is `control.html`.
- **Name:** "Personal Organizer" (the Docker app stays "Home Organizer"). The Control
  page subtitle is "Personal organizer" (it was "Shared household organizer").
- **Auto-reload after a redeploy** is now handled by the service worker. See
  "Shipping an update" in the README.

**New**

- Works offline and installs to the home screen.
- A Backup panel on the Control page, with export, import, merge and replace.
- An app icon.
- A place in `backup.js` (`EXPORTERS`) ready for a future `.ics` calendar export.

**Unchanged:** the look of every screen, the coloured dots, the agenda, the checklist,
notes, month navigation and the "Today" button.
