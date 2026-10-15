#!/usr/bin/env bash
# Restore or save the migrated test database template, as a pg_dump.
#
#   test-db-template.sh restore <postgres-container> <template-db> <dump-file>
#   test-db-template.sh save    <postgres-container> <template-db> <dump-file>
#
# WHY: building the template is running every migration, and on a CI runner that
# was ~55s of the pytest job's critical path -- one worker migrates while the
# other three wait on its lock (connect_labs/testing/db_template.py). It is
# Python, not Postgres: locally the migrate is 28s of CPU in 37s of wall, so
# tuning the server would not move it. Restoring the same database from a dump
# takes a few seconds, and the result is byte-identical (schema AND the rows the
# data migrations seed -- currencies, countries, celery beat tasks, content
# types, permissions), checked by diffing pg_dump of both.
#
# pg_dump/pg_restore run INSIDE the service container, so their version always
# matches the server's; the runner's own client tools are an older major and
# refuse to dump a newer server.
#
# Neither mode can fail the job. A restore that goes wrong drops what it made,
# and pytest builds the template from migrations exactly as it did before this
# existed. A save that goes wrong just means the next run builds it too.

set -uo pipefail

mode=${1:?mode}
container=${2:?postgres container id}
template=${3:?template database name}
dump=${4:?dump file}

psql() { docker exec -i "$container" psql -U postgres -v ON_ERROR_STOP=1 -qtA "$@"; }

wait_for_postgres() {
  # Over TCP on purpose. The image's first start runs its init scripts against a
  # temporary server that listens on the unix socket only, then restarts; a
  # socket check can pass in that window and the restore lands on a server
  # that is about to go away.
  for _ in $(seq 1 60); do
    docker exec "$container" pg_isready -h 127.0.0.1 -U postgres -q && return 0
    sleep 1
  done
  echo "::warning::Postgres not ready after 60s; pytest will build the test database itself"
  return 1
}

case "$mode" in
restore)
  [ -s "$dump" ] || { echo "No dump at $dump"; exit 0; }
  wait_for_postgres || exit 0
  start=$(date +%s%3N)
  psql -c "DROP DATABASE IF EXISTS \"$template\"" -c "CREATE DATABASE \"$template\"" || exit 0
  # --single-transaction: all of it or none of it. A half-restored template
  # would be trusted by --reuse-db and cloned into every worker.
  if docker exec -i "$container" pg_restore -U postgres -d "$template" \
    --no-owner --single-transaction --exit-on-error <"$dump"; then
    rows=$(psql -d "$template" -c "SELECT count(*) FROM django_migrations")
    echo "Restored $template from cache in $(($(date +%s%3N) - start))ms ($rows migrations recorded)"
  else
    echo "::warning::Restoring the test database template failed; pytest will migrate from scratch"
    psql -c "DROP DATABASE IF EXISTS \"$template\"" || true
  fi
  ;;
save)
  exists=$(psql -c "SELECT 1 FROM pg_database WHERE datname = '$template'") || exit 0
  if [ "$exists" != 1 ]; then
    echo "::warning::No $template database to save (did the run use LABS_TEST_DB_TEMPLATE=1 with xdist?)"
    exit 0
  fi
  mkdir -p "$(dirname "$dump")"
  if docker exec "$container" pg_dump -U postgres -Fc -d "$template" >"$dump.tmp"; then
    mv "$dump.tmp" "$dump"
    echo "Dumped $template: $(du -h "$dump" | cut -f1)"
  else
    echo "::warning::pg_dump of $template failed; not caching it"
    rm -f "$dump.tmp"
  fi
  ;;
*)
  echo "unknown mode: $mode" >&2
  exit 2
  ;;
esac

# Never fail the job (see the header).
exit 0
