# Browser tests

These Playwright tests drive the real app in Chrome, the way a person would. They cover Batch 1 (register, log in and out, staying logged in, role-based pages) and Batch 2 (add, list, edit, delete, anomaly warning and lock, range settings).

## What they need

- The stack running locally: `docker compose up -d` from `monitoring-application/`.
- Google Chrome installed. The tests use it directly, so Playwright needs no browser download. To use Playwright's own Chromium instead, run `.venv/bin/playwright install chromium` once and set `E2E_BROWSER_CHANNEL=` (empty).
- Docker access to the stack. The app has no API to change roles yet, so the tests set roles through `db_tool.py` inside the backend container.

## Set up once

From `monitoring-application/e2e/`:

```sh
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -r requirements.txt
```

## Run

```sh
.venv/bin/python -m pytest
```

Useful options:

- `--headed --slowmo 300`: watch the browser.
- `-k edit`: run tests whose names contain `edit`.
- `--screenshot only-on-failure --output <folder>`: keep a screenshot of each failure.

Settings, all optional:

| Variable | Default | Use |
| --- | --- | --- |
| `E2E_BASE_URL` | `http://127.0.0.1:18501` | Frontend address |
| `E2E_API_URL` | `http://127.0.0.1:18000` | Backend address |
| `E2E_ENV_FILE` | none, so Compose reads `.env` | Env file the stack was started with |
| `E2E_BROWSER_CHANNEL` | `chrome` | Browser to use; empty means Playwright's Chromium |
| `E2E_KEEP_DATA` | unset | `1` keeps the run's accounts and records for inspection |

## Data

Each run registers its own accounts and creates records in its own categories, all named with a random `e2e-<id>` prefix (categories start with `!e2e-<id>` so they list first). At the end it deletes everything with that prefix, including anomalous records, which the app itself never allows. `db_tool.py` refuses to delete anything whose prefix doesn't start with `e2e-`.

Only run these tests against a local or test stack. They write to whatever database the stack uses.

## Not covered here

- A save that times out and is retried: that needs a 30-second stall. The backend side is checked by `test_retrying_a_save_with_the_same_key_adds_one_record` and by the backend suite.
- Backend outages, phone widths, screen readers and keyboard focus.
- Anything visual: the list's anomaly marks and range bars are drawn on a canvas that these tests can't read, so they check the action row and the API instead.
