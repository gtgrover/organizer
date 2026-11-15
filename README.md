# Home Organizer — local prototype

A landscape dashboard and phone controller backed by FastAPI and SQLite. No external scripts, cloud services or outbound API calls are used by the application.

## Start on Intel Mac (Docker Desktop installed)

```sh
unzip home-organizer.zip
cd home-organizer
docker compose up --build -d
```

Open `http://localhost:8280/display` and `http://localhost:8280/control` on the Mac.

For phones on the same trusted Wi-Fi, find the Mac's LAN IP in System Settings → Network and open `http://MAC-IP:8280/control`. Keep the Mac awake. If macOS asks whether Docker may accept incoming connections, allow only on the trusted network.

Stop: `docker compose down` (does not delete `data/`). Logs: `docker compose logs -f`. Update after editing code: `docker compose up --build -d`.

## Security and current limits

**Prototype only:** There is no user authentication or protected admin interface. Any client able to reach port 8280 can edit or delete household data and change the wall view. Run only on a trusted LAN, restrict inbound access using host/network firewall rules, do not forward the port, and keep guest Wi-Fi isolated. The app does not fetch from the internet; Docker image build requires internet for Python packages. Network-level outbound isolation is **not configured** by this Compose file. A device's browser or operating system may still use the internet independently.

The `/admin` page is a placeholder; there is no holiday importer yet. Event and task editing are basic; no recurring events, multiple named lists, or user accounts yet. The app uses a single shared display view, not per-screen targeting. Data is stored in `./data/organizer.db`. `docker compose down -v` is not needed.

## Backups

**Control → Backup → Export backup file** downloads everything (events, checklist, notes) as one JSON file. **Import** loads a file back: *Merge* adds what's new and keeps the most recently edited copy of anything in both (nothing is deleted, importing twice changes nothing); *Replace all* wipes and loads the file. The file format is the same as the offline Personal Organizer's (`pwa/`), so backups move freely between the two apps in either direction.

You can also copy `./data/organizer.db` while the container is stopped (or with `sqlite3 data/organizer.db ".backup copy.db"` while it runs).

## Architecture

- Python FastAPI / Uvicorn, SQLite, static HTML/CSS/JS
- `/display`: landscape screen
- `/control`: phone-friendly editor, wall remote, printing and backup
- `/admin`: placeholder
- WebSocket broadcasts full updated state to connected browsers
- Docker build works on x86_64 and arm64 if the target supports the Python base image and Docker

## Two apps, one set of screens

This project contains two versions with identical screens:

| | Docker **Home Organizer** (`app/`) | Offline **Personal Organizer** (`pwa/`) |
|---|---|---|
| Data | Shared by the household, on the server | Only on each device (IndexedDB) |
| Runs | `docker compose up --build -d` | Static files, see `pwa/README.md` |
| Data layer | `app/static/db-server.js` | `pwa/db.js` |

**Edit the screens in `shared/` only.** The pages, CSS, drawing code, printing and backup panel live there. Then run:

```sh
python3 tools/sync.py           # copies shared/ into app/static/ and pwa/
docker compose up --build -d    # if you want the Docker app updated now
```

Before deploying the offline app, also bump `VERSION` in `pwa/sw.js`. Files generated from `shared/` start with a `GENERATED` notice, so don't edit those copies. `python3 tools/sync.py --check` reports anything out of date.

The few real differences between the apps (name, links, head tags, data layer, backup wording) are settings at the top of `tools/sync.py`. Both data layers provide the same `DB.*` functions, so shared code must only use those.

**Tests** (see `tools/README.md`): `tools/verify_docker.py` runs the Docker app in a throwaway container on a copy of your data, and `tools/verify_pwa.py` tests the offline app. Both check that the two apps look pixel-identical.
