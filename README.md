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

The `/admin` page is a placeholder; there is no holiday importer yet. Event and task editing are basic; no recurring events, multiple named lists, user accounts, or backups yet. The app uses a single shared display view, not per-screen targeting. Data is stored in `./data/organizer.db`; back up that file while the container is stopped (or use SQLite's online backup API). `docker compose down -v` is not needed.

## Architecture

- Python FastAPI / Uvicorn, SQLite, static HTML/CSS/JS
- `/display`: landscape screen
- `/control`: phone-friendly editor and wall remote
- `/admin`: placeholder
- WebSocket broadcasts full updated state to connected browsers
- Docker build works on x86_64 and arm64 if the target supports the Python base image and Docker
