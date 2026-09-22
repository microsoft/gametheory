#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
export PYTHONPATH="$PWD/src"

sql=false
browser=false
for argument in "$@"; do
  case "$argument" in
    --sql) sql=true ;;
    --browser) browser=true ;;
    *) echo "Usage: bash scripts/verify.sh [--sql] [--browser]" >&2; exit 2 ;;
  esac
done

schema="${FLOOD_LAB_CATALOG_SCHEMA:-../../backend/contracts/operation-catalog-v1.schema.json}"
if [[ ! -f "$schema" ]]; then
  echo "The foreign OperationCatalog schema artifact is required; set FLOOD_LAB_CATALOG_SCHEMA." >&2
  exit 2
fi
export FLOOD_LAB_CATALOG_SCHEMA="$schema"

if $sql; then
  : "${FLOOD_LAB_TEST_DATABASE_URL:?Select a dedicated disposable lab SQL target}"
  : "${FLOOD_LAB_TEST_DATABASE_NAME:?Name the dedicated disposable lab SQL database}"
  if [[ "$FLOOD_LAB_TEST_DATABASE_NAME" != flood_lab_test_* ||
        "${FLOOD_LAB_TEST_ALLOW_RESET:-}" != yes ]]; then
    echo "Real SQL verification requires flood_lab_test_* and explicit disposable-target consent." >&2
    exit 2
  fi
fi

.venv/bin/ruff check src migrations tests
.venv/bin/ruff format --check src migrations tests
.venv/bin/python -m pytest tests/unit
.venv/bin/python -m flood_lab.assets --check
.venv/bin/python -m flood_lab.openapi --check
npm --prefix ui run test
npm --prefix ui run build

if $sql; then
  .venv/bin/python -m pytest tests/sql
else
  echo "SQL integration NOT RUN. Use --sql with a separately authorized disposable SQL target."
fi
if $browser; then
  mkdir -p .local/build
  export TMPDIR="$PWD/.local/build"
  export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-$PWD/.local/browsers}"
  npm --prefix ui run test:browser
fi
