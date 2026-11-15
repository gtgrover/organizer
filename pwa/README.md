# Personal Organizer (offline app)

A personal calendar, checklist and notes app that runs entirely in the browser.
No server, no accounts, no internet needed after the first visit. **Each person's
data stays only on their own device.** To move or share data, use
*Control → Backup → Export*, and *Import* on the other device.

This folder is the whole app: plain HTML, CSS and JavaScript with no libraries,
no build step and nothing loaded from a CDN.

**Editing:** most of these files are generated from `../shared/` by `tools/sync.py`
(they start with a `GENERATED` notice), so make screen changes there. See the README
in the project root. Files specific to this app: `db.js`, `sw.js`, `pwa.js`,
`manifest.webmanifest` and `icons/`.

| File | What it is |
|---|---|
| `index.html` | The display: calendar, agenda, checklist, notes |
| `control.html` | Add and edit events, checklist, notes; print; backup |
| `db.js` | Local database (IndexedDB) |
| `common.js`, `common.css` | Shared page code and styling (same as the original app) |
| `print.css` | Printable month calendar (US Letter, landscape) |
| `backup.js` | Export and import of backup files |
| `sw.js`, `pwa.js`, `manifest.webmanifest`, `icons/` | Offline support and installing |

## Test it on your computer

Service workers only run on `localhost` or HTTPS, so open the app through a local web
server, not by double-clicking the file:

```sh
cd pwa
python3 -m http.server 8000
```

Open <http://localhost:8000/>. To test offline: Chrome DevTools → **Application** →
**Service workers** → tick **Offline**, then reload.

Opening it from another device on your Wi-Fi (`http://192.168.x.x:8000`) shows the
pages, but offline mode and installing won't work there, because that address isn't
HTTPS. Use a real deployment for phones.

## Publish it

Any static host works. Upload the **contents** of this folder.

**GitHub Pages**
1. Create a repository (for example `personal-organizer`) and put the contents of `pwa/`
   at its top level.
2. Repository → **Settings → Pages** → *Deploy from a branch* → `main`, folder `/ (root)`.
3. After a minute it's live at `https://<your-user>.github.io/personal-organizer/`.

All paths are relative, so it works in a sub-folder like that.

**Cloudflare Pages:** Workers & Pages → Create → Pages → *Upload assets* → drag in the
`pwa` folder. There's no build command.

### Shipping an update

1. Make your changes.
2. **Change `VERSION` at the top of `sw.js`** (for example `2026-11-15.1` → `2026-11-20.1`).
3. Deploy.

Browsers notice the new `sw.js`, download the new files, and open copies of the app
reload themselves. A page with half-typed input waits until that's saved or cleared.
Pages that stay open, like a wall display, check for updates every hour. **If you
forget to change `VERSION`, people keep the old version.** Nobody's data is touched
by an update.

## Install it (recommended for everyone)

**iPhone / iPad:** open the site in **Safari** → **Share** button → **Add to Home
Screen** → **Add**. Then always open it from the home-screen icon.

**Android (Chrome):** menu ⋮ → **Install app** (or *Add to Home screen*).

**Mac / Windows (Chrome or Edge):** click the install icon at the right end of the
address bar. **Mac Safari:** File → **Add to Dock**.

**Why install?** Safari can **delete a website's stored data if you haven't opened the
site for a few weeks**, and it treats a plain browser tab that way. A home-screen app
is treated as an app, and its data is kept. On other browsers, installing also makes
it much less likely the browser will clear the data to free up space. (The app also
asks the browser to keep its storage permanently, but browsers may decline.)

Even when installed, the data lives in one place only. **Export a backup now and
then** (Control → Backup), and save it somewhere safe such as Files, iCloud Drive or
email.

Note for iPhone: a home-screen app and Safari keep **separate** data. Something added
in a Safari tab won't appear in the home-screen app, so pick one (the home-screen app).

## Backups and sharing

**Export** saves a file like `personal-organizer-backup-2026-11-15-0930.json`. On a phone
or tablet this opens the Share sheet (*Save to Files*, AirDrop, Mail…); on a computer
it downloads the file.

**Import** has two modes:

- **Merge (default):** adds anything new from the file. If an item exists in both, the
  copy edited most recently wins. Nothing is deleted. Notes from the file are added
  below yours unless they're already there. Importing the same file twice does nothing
  the second time.
- **Replace all:** deletes everything on this device and loads the file. It asks
  first.

Merge never deletes anything. If you delete an event and then merge in an older backup
that still has it, the event comes back.

The file is plain JSON with `app`, `schemaVersion` (currently `1`), `exportedAt`, and
`data` (`events`, `tasks`, `notes`, `settings`). Dates are stored as `YYYY-MM-DD`
text, so they come back on the same day in any timezone. Bad files are rejected with
a message, and nothing on the device changes.

## Moving data from the old Docker app

From the project root (one level up from this folder):

```sh
python3 tools/export_for_pwa.py data/organizer.db personal-organizer-migrated.json
```

This only reads the database, so it's safe while the container is running. Then in
the app: **Control → Backup → Import** that file. Use *Replace* on a fresh install, or
*Merge* to add it to existing data. Running the script again and merging gives no
duplicates.

## Printing

**Control → Month to print → Print selected month** prints the same letter-landscape
month grid as the original app. Choose *Save as PDF* in the print dialog to get a
file.
