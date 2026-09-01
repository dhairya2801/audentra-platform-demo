#!/usr/bin/env bash
# Recreate the writable write-eval database from the migrated university base.
#
# A suite that grades canonical effects has to start from a known row set, so
# this runs before every batch. The base template is built once by README §Setup
# and is never written to.
set -euo pipefail
PG="${WRITE_EVAL_PG_CONTAINER:-audentra-platform-postgres-1}"
BASE="${WRITE_EVAL_BASE_DB:-vv_enrollment_write_base}"
TARGET="${WRITE_EVAL_DB:-vv_enrollment_write_eval}"
docker exec "$PG" psql -U vv -d postgres -q -c \
  "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$TARGET' AND pid<>pg_backend_pid();" >/dev/null
docker exec "$PG" psql -U vv -d postgres -q -c "DROP DATABASE IF EXISTS $TARGET;"
docker exec "$PG" psql -U vv -d postgres -q -c "CREATE DATABASE $TARGET TEMPLATE $BASE OWNER vv;"
