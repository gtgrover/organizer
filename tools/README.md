# tools/

Helper scripts for the offline app in `../pwa/`. They aren't part of the published app.

## export_for_pwa.py: move data out of the Docker app

```sh
python3 tools/export_for_pwa.py data/organizer.db personal-organizer-migrated.json
```

This only reads the database, using Python's standard library, so it's safe to run
while the container is up. Then in the app: **Control → Backup → Import**.

## verify_pwa.py: automated end-to-end test

Run this after changing anything in `pwa/`, before deploying.

One-time setup (it uses its own Python environment and its own copy of Chromium, and
never touches your browsers or their data):

```sh
python3 -m venv .venv-test
.venv-test/bin/pip install playwright pillow
.venv-test/bin/playwright install chromium
```

Run it from the project root:

```sh
.venv-test/bin/python tools/verify_pwa.py
```

It prints PASS or FAIL for each check and ends with a total, such as `43/43 passed`.
It exits with 0 only if every check passed.

What it covers:
- adding, editing and deleting events and checklist items
- notes, reloading, and offline use
- export, wiping storage, and import (replace and merge)
- timezones and app updates
- importing your real data through `export_for_pwa.py`

If the Docker app is running, it also checks that the display and the printed month
are pixel-identical to the old app. Otherwise it skips that check. To point it at a
different address, set `OLD_APP_URL=http://host:port`.

Screenshots and test files go to `tools/test-output/`. The folder is replaced on each
run and is safe to delete.
