# MonitoringDashboard

## Run

Requires Docker with Docker Compose v2.

```sh
cp .env.example .env    # then fill in every required value
docker compose up -d --build
```

| Service | Address (default) |
| --- | --- |
| Frontend | http://127.0.0.1:18501 |
| Backend Swagger | http://127.0.0.1:18000/docs |

Database data lives in the `db_data` volume and survives `docker compose down`. `docker compose down -v` deletes it.

- Every `docker compose` command reads `.env`, including `down`, and stops with an error naming the variable if a required one is missing.
- MariaDB applies `MARIADB_*` credentials only when the volume is first created. To change them later, change them in the database as well, or recreate the volume, which deletes the data.
- Schema migrations run automatically each time the backend container starts.
- The initial Admin is created from `INITIAL_ADMIN_USERNAME` and `INITIAL_ADMIN_PASSWORD` when that account does not exist. Once it exists, these values are not applied, so changing them does not change the account. A password that does not meet the registration rules still works, but the backend logs an `initial_admin_weak_password` warning naming the missing conditions. Use one with at least 8 characters, including uppercase, lowercase, a digit and a symbol.

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
