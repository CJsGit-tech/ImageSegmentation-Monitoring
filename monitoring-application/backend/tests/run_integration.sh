#!/bin/sh
# Runs the whole backend test suite against a throwaway MariaDB, then removes everything it made.
# Only touches the uniquely named container and network created here.
set -eu

backend_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
suffix="$$-$(date +%s)"
network="monitoring-backend-test-net-$suffix"
database="monitoring-backend-test-db-$suffix"
password="throwaway-test-password"

cleanup() {
    docker rm -f "$database" >/dev/null 2>&1 || true
    docker network rm "$network" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

docker build --quiet --target test -t monitoring-dashboard-backend-test "$backend_dir" >/dev/null
docker network create "$network" >/dev/null
docker run -d --name "$database" --network "$network" \
    -e MARIADB_RANDOM_ROOT_PASSWORD=1 \
    -e MARIADB_DATABASE=monitoring_test \
    -e MARIADB_USER=monitoring \
    -e MARIADB_PASSWORD="$password" \
    mariadb:11.7.2 \
    --character-set-server=utf8mb4 --collation-server=utf8mb4_uca1400_ai_ci >/dev/null

docker run --rm --network "$network" \
    -e TEST_DB_HOST="$database" \
    -e TEST_DB_PASSWORD="$password" \
    monitoring-dashboard-backend-test "$@"
