# tools/

Helper scripts. None of them are part of either app.

## sync.py: copy the shared screens into both apps

```sh
python3 tools/sync.py           # write app/static/ and pwa/ from shared/
python3 tools/sync.py --check   # just report anything out of date
```

Edit screens in `shared/`, never the generated copies. The per-app differences are
settings at the top of the script.

## export_for_pwa.py: move data out of the Docker app

```sh
python3 tools/export_for_pwa.py data/organizer.db personal-organizer-migrated.json
```

This only reads the database, using Python's standard library, so it's safe to run
while the container is up. Then in the app: **Control → Backup → Import**.

## verify_pwa.py and verify_docker.py: automated end-to-end tests

Run both after changing anything in `shared/` (or either app), before deploying.

One-time setup (it uses its own Python environment and its own copy of Chromium, and
never touches your browsers or their data):

```sh
python3 -m venv .venv-test
.venv-test/bin/pip install playwright pillow
.venv-test/bin/playwright install chromium
```

Run it from the project root:

```sh
.venv-test/bin/python tools/verify_pwa.py      # offline app
.venv-test/bin/python tools/verify_docker.py   # Docker app (Docker must be running)
```

`verify_docker.py` builds the image and runs it as a **throwaway container on port 8281
with a copy of `data/organizer.db`**. Your real container and data are never touched.
It covers the database upgrade, editing, live sync between open pages, export/import,
and backups moving between the Docker app and the offline app in both directions.
Run it before `docker compose up --build -d`, so its pixel comparison is against the
version you're currently running.

Each prints PASS or FAIL for each check and ends with a total, such as `43/43 passed`.
It exits with 0 only if every check passed.

What `verify_pwa.py` covers:
- adding, editing and deleting events and checklist items
- notes, reloading, and offline use
- export, wiping storage, and import (replace and merge)
- timezones and app updates
- importing your real data through `export_for_pwa.py`

If the Docker app is running, it also checks that the display and the printed month
are pixel-identical to the old app. Otherwise it skips that check. To point it at a
different address, set `OLD_APP_URL=http://host:port`.

Screenshots and test files go to `tools/test-output/pwa/` and `tools/test-output/docker/`.
They're replaced on each run and are safe to delete.
