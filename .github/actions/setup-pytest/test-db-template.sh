#!/usr/bin/env bash
# Restore or save the migrated test database template, as a pg_dump.
#
#   test-db-template.sh tune          <postgres-container>
#   test-db-template.sh restore       <postgres-container> <template-db> <dump-file>
#   test-db-template.sh restore-older <postgres-container> <template-db> <dump-file> <its-manifest> <this-manifest>
#   test-db-template.sh save          <postgres-container> <template-db> <dump-file> [<manifest-to-store>]
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
template=${3:-}
dump=${4:-}

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

restore_dump() {
  [ -s "$dump" ] || { echo "No dump at $dump"; return 1; }
  wait_for_postgres || return 1
  start=$(date +%s%3N)
  psql -c "DROP DATABASE IF EXISTS \"$template\"" -c "CREATE DATABASE \"$template\"" || return 1
  # Roles live in the cluster, not the dump, and the dump grants to and writes
  # policies for labs_data_reader (labs migration 0039). A fresh service container
  # has no such role, so without this every restore fails and migrates cold.
  psql -c "DO \$\$ BEGIN CREATE ROLE labs_data_reader NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END \$\$" || return 1
  # --single-transaction: all of it or none of it. A half-restored template
  # would be trusted by --reuse-db and cloned into every worker.
  if docker exec -i "$container" pg_restore -U postgres -d "$template" \
    --no-owner --single-transaction --exit-on-error <"$dump"; then
    rows=$(psql -d "$template" -c "SELECT count(*) FROM django_migrations")
    echo "Restored $template from cache in $(($(date +%s%3N) - start))ms ($rows migrations recorded)"
  else
    echo "::warning::Restoring the test database template failed; pytest will migrate from scratch"
    psql -c "DROP DATABASE IF EXISTS \"$template\"" || true
    return 1
  fi
}

case "$mode" in
tune)
  # A throwaway database on a throwaway runner: nothing here needs to survive a
  # crash, so stop paying for durability. Every commit (TransactionTestCase and
  # transaction=True tests commit, then flush every table) otherwise waits on
  # an fsync to the runner's disk. All three settings take effect on reload.
  wait_for_postgres || exit 0
  psql -c "ALTER SYSTEM SET fsync = off" \
    -c "ALTER SYSTEM SET synchronous_commit = off" \
    -c "ALTER SYSTEM SET full_page_writes = off" \
    -c "SELECT pg_reload_conf()" >/dev/null &&
    echo "Postgres durability off: $(psql -c 'SHOW fsync') fsync, $(psql -c 'SHOW synchronous_commit') synchronous_commit"
  ;;
restore)
  restore_dump
  ;;
restore-older)
  # A dump cached under an OLDER key: the migrations have moved on since it was
  # made. About half of all runs used to land here -- 25 of 146 commits to main
  # over three days changed a migration or a setting -- and each paid a full
  # cold migrate (1-2 minutes) for the sake of the one or two migrations that
  # were new. Instead: restore the old template and apply just those.
  #
  # Only when that is provably what a cold migrate would give. Each manifest
  # lists the sha256 of every input to the cache key. Every file the old dump
  # was built from must be byte-identical now, and every file that is new must
  # be a new migration. An EDITED migration (already recorded as applied, so
  # migrate would not re-run it), a changed setting, requirement or helper
  # module, or a deleted migration all fall through to the cold path.
  old=${5:?manifest of the cached dump}
  new=${6:?manifest of this checkout}
  [ -s "$old" ] && [ -s "$new" ] || { echo "No manifest to compare; migrating from scratch"; exit 0; }
  changed=$(comm -23 <(sort "$old") <(sort "$new"))
  added=$(comm -13 <(cut -c67- "$old" | sort) <(cut -c67- "$new" | sort))
  not_migrations=$(printf '%s\n' "$added" | grep -v '^$' | grep -v '/migrations/' || true)
  if [ -n "$changed" ] || [ -n "$not_migrations" ]; then
    echo "The cached template was built from files that have changed since; migrating from scratch:"
    printf '%s\n' "$changed" "$not_migrations" | grep -v '^$' | sed 's/^/  /' | head -20
    exit 0
  fi
  echo "New since the cached template:"
  printf '%s\n' "$added" | sed 's/^/  /'
  restore_dump || exit 0
  start=$(date +%s%3N)
  if DATABASE_URL="postgres://postgres:postgres@localhost:5432/$template" \
    DJANGO_SETTINGS_MODULE=config.settings.test .venv/bin/python manage.py migrate --noinput; then
    echo "Applied the new migrations to $template in $(($(date +%s%3N) - start))ms"
  else
    echo "::warning::Migrating the cached test database template forward failed; pytest will migrate from scratch"
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
  # An older dump restored from cache may be sitting here. It must not survive a
  # failed dump and be cached under this run's key.
  rm -f "$dump" "$(dirname "$dump")/manifest.txt"
  manifest=${5:-}
  if docker exec "$container" pg_dump -U postgres -Fc -d "$template" >"$dump.tmp"; then
    mv "$dump.tmp" "$dump"
    # What this dump was built from, for a later run's restore-older to compare.
    if [ -n "$manifest" ]; then cp "$manifest" "$(dirname "$dump")/manifest.txt"; fi
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
