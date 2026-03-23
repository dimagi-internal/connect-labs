#!/usr/bin/env bash
#
# check_dev_environment.sh — validate the local dev environment before starting the backend.
# Usage: bash scripts/check_dev_environment.sh
#        inv check
#
# Checks:
#   1. Virtual environment exists and is activated
#   2. python3 binary is accessible
#   3. Ports 8000-8002 are free
#   4. Required Python imports work (Django, DRF, Celery, Redis, etc.)
#
# For each failure, prints a one-line fix command.

set -uo pipefail

PASS=0
FAIL=0
WARN=0

pass() {
    printf "  \033[32m✓\033[0m %s\n" "$1"
    ((PASS++))
}

fail() {
    printf "  \033[31m✗\033[0m %s\n" "$1"
    printf "    Fix: %s\n" "$2"
    ((FAIL++))
}

warn() {
    printf "  \033[33m!\033[0m %s\n" "$1"
    printf "    Fix: %s\n" "$2"
    ((WARN++))
}

header() {
    printf "\n\033[1m%s\033[0m\n" "$1"
}

# ---------------------------------------------------------------------------
header "Virtual environment"
# ---------------------------------------------------------------------------

VENV_DIR=""
if [ -d ".venv" ]; then
    VENV_DIR=".venv"
elif [ -d "venv" ]; then
    VENV_DIR="venv"
fi

if [ -n "$VENV_DIR" ]; then
    pass "Virtual environment directory found ($VENV_DIR/)"
else
    fail "No venv/ or .venv/ directory found" \
         "python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements-dev.txt"
fi

if [ -n "${VIRTUAL_ENV:-}" ]; then
    pass "Virtual environment is activated ($VIRTUAL_ENV)"
else
    activate_path="${VENV_DIR:-venv}/bin/activate"
    fail "Virtual environment is not activated" \
         "source $activate_path"
fi

# ---------------------------------------------------------------------------
header "Python"
# ---------------------------------------------------------------------------

if command -v python3 &>/dev/null; then
    py_version=$(python3 --version 2>&1)
    pass "python3 is accessible ($py_version)"
else
    fail "python3 not found on PATH" \
         "brew install python@3.11   # or install from python.org"
fi

# ---------------------------------------------------------------------------
header "Ports (8000-8002)"
# ---------------------------------------------------------------------------

for port in 8000 8001 8002; do
    if lsof -iTCP:"$port" -sTCP:LISTEN -t &>/dev/null; then
        pid=$(lsof -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null | head -1)
        proc=$(ps -p "$pid" -o comm= 2>/dev/null || echo "unknown")
        fail "Port $port is in use (pid $pid — $proc)" \
             "kill $pid   # or: lsof -iTCP:$port -sTCP:LISTEN"
    else
        pass "Port $port is free"
    fi
done

# ---------------------------------------------------------------------------
header "Required Python packages"
# ---------------------------------------------------------------------------

check_import() {
    local module="$1"
    local pip_name="${2:-$1}"
    if python3 -c "import $module" 2>/dev/null; then
        pass "$module importable"
    else
        fail "$module is not importable" \
             "pip install $pip_name"
    fi
}

check_import django Django
check_import rest_framework djangorestframework
check_import celery celery
check_import redis redis
check_import httpx httpx
check_import environ django-environ
check_import crispy_forms django-crispy-forms
check_import psycopg2 psycopg2-binary

# ---------------------------------------------------------------------------
header ".env file"
# ---------------------------------------------------------------------------

if [ -f ".env" ]; then
    pass ".env file exists"
else
    warn ".env file is missing — Django settings will fail to load" \
         "cp .env_template .env   # then fill in values"
fi

# ---------------------------------------------------------------------------
header "Docker services (postgres, redis)"
# ---------------------------------------------------------------------------

if command -v docker &>/dev/null; then
    if docker compose ps --status running 2>/dev/null | grep -q "db"; then
        pass "PostgreSQL container is running"
    else
        warn "PostgreSQL container is not running" \
             "inv up   # or: docker compose up -d"
    fi
    if docker compose ps --status running 2>/dev/null | grep -q "redis"; then
        pass "Redis container is running"
    else
        warn "Redis container is not running" \
             "inv up   # or: docker compose up -d"
    fi
else
    warn "Docker is not installed — needed for postgres and redis" \
         "brew install --cask docker"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

printf "\n\033[1mSummary: %d passed, %d failed, %d warnings\033[0m\n" "$PASS" "$FAIL" "$WARN"

if [ "$FAIL" -gt 0 ]; then
    printf "\033[31mEnvironment is not ready. Fix the failures above before starting the backend.\033[0m\n"
    exit 1
else
    printf "\033[32mEnvironment looks good. Run: python manage.py runserver\033[0m\n"
    exit 0
fi
