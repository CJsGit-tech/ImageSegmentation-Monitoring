# MonitoringDashboard

## Run

Requires Git, Docker and Docker Compose v2. Get the project from GitHub:

```sh
git clone https://github.com/CJsGit-tech/ImageSegmentation-Monitoring.git
cd ImageSegmentation-Monitoring/monitoring-application
```

Create the environment file, then fill in the database passwords and `JWT_SECRET_KEY` (the three test accounts already have example values):

```sh
cp .env.example .env
```

Build and start the database, backend and frontend:

```sh
docker compose up -d --build
```

Check that all three services are healthy:

```sh
docker compose ps
```

| Service | Address (default) |
| --- | --- |
| Frontend | http://127.0.0.1:18501 |
| Backend API | http://127.0.0.1:18000 |
| Backend Swagger | http://127.0.0.1:18000/docs |
| Database | Not published outside Docker |

Once it is running:

1. Open http://127.0.0.1:18501 and log in with any account from [the test accounts](../README.md#測試帳號資訊).
2. As User or Admin, go to 資料 → 批量導入 in the sidebar and upload `docs/sample-records-1000.csv` to load 1000 sample records.
3. The 即時監控 page receives live data over WebSocket and updates every second. Simulated data appears about 1 second after startup.

Follow the backend logs:

```sh
docker compose logs -f backend
```

Stop the services and keep the database:

```sh
docker compose down
```

Database data lives in the `db_data` volume and survives `docker compose down`. `docker compose down -v` deletes it.

- Every `docker compose` command reads `.env`, including `down`, and stops with an error naming the variable if a required one is missing.
- MariaDB applies `MARIADB_*` credentials only when the volume is first created. To change them later, change them in the database as well, or recreate the volume, which deletes the data.
- Schema migrations run automatically each time the backend container starts.
- The backend generates simulated readings for 4 devices, one per device every `SIMULATOR_INTERVAL_SECONDS` (default 1, so one reading per device per second). The value is read when the backend starts, so restart it after a change: `docker compose up -d backend`. Simulated readings are kept in the database without a time limit; at the default rate that is about 345,000 rows a day.
- The monitoring screen's live window receives readings over the backend WebSocket (`/api/v1/realtime/ws`): the Streamlit server holds one connection per viewer that shows the live window and redraws every second. A connection nobody is looking at closes after 30 seconds. `GET /api/v1/realtime/window` returns the same snapshot over REST.
- Three test accounts, one per role, come from `.env`: `INITIAL_ADMIN_*` (Admin), `INITIAL_USER_*` (User) and `INITIAL_VIEWER_*` (Viewer). All six variables are required, and the three usernames must differ. `.env.example` has example values (`admin` / `Admin@12345`, `user` / `User@12345`, `viewer` / `Viewer@12345`); change them for anything other than local testing. Each account is created at backend start when it does not exist. Once it exists, these values are not applied, so changing them does not change its password or role. A password that does not meet the registration rules still works, but the backend logs an `initial_<role>_weak_password` warning naming the missing conditions. Use one with at least 8 characters, including uppercase, lowercase, a digit and a symbol.
- The system log (Admin, `GET /api/v1/logs`) keeps logins, logouts, permission denials, role changes, record changes, import results, range and unit changes and system errors. It never keeps simulated readings. Rows older than `LOG_RETENTION_DAYS` (default 365) are removed automatically, at backend start and then at most hourly. Restart the backend after changing it: `docker compose up -d backend`.
- Every system log event is also written to the backend's stdout as a JSON line. Rows reach the database in batches about once a second, so a backend crash can lose roughly the last second of rows from the database, though not from stdout. While the database is unavailable, rows wait in memory (up to 10,000; beyond that the oldest are dropped, with a warning) and are written when it recovers.

## API documentation

[`docs/api-documentation.html`](docs/api-documentation.html) is a Swagger UI copy of the API documentation. Download it and open it in a browser. It needs internet access to load Swagger UI, but no running backend. "Try it out" is turned off in this copy, so use the Swagger address above to send requests. All endpoints are under `/api/v1/`, and the WebSocket (`/api/v1/realtime/ws`) isn't listed, because OpenAPI doesn't cover WebSockets.

After changing the API, regenerate the file from this folder:

```sh
docker build --target test -t monitoring-dashboard-backend-test backend
docker run --rm -v "$PWD/docs:/out" monitoring-dashboard-backend-test python -m scripts.export_api_docs /out/api-documentation.html
```

## Frontend

The frontend is a Streamlit app. `frontend/asgi.py` is the server entry point and `frontend/app.py` is the shell that every page runs through. Screens live in `frontend/screens/`. Do not rename that folder to `pages/`: Streamlit then lists every file in the sidebar, including admin pages, during the first session after a server start.

Login is stored in an HttpOnly cookie, `md_session`, so it survives a refresh or a new tab. It has no `Secure` attribute over plain HTTP, and browsers send cookies to every port on a host. Keep the default `HOST_BIND_ADDRESS=127.0.0.1` unless the frontend is behind TLS on its own hostname. The frontend must run as a single replica.

## Test

```sh
docker build --target test -t monitoring-dashboard-backend-test backend
docker run --rm monitoring-dashboard-backend-test
```

This needs no database, so tests that do are skipped with a stated reason. To run everything, use the script below. It starts a throwaway MariaDB container on a temporary network and removes both when it finishes:

```sh
backend/tests/run_integration.sh
```

Browser tests that click through the running app are in `e2e/`. See [e2e/README.md](e2e/README.md) for setup.
