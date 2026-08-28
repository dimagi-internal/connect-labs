.PHONY: commit test manage

# The main checkout, asked of git rather than guessed. From a worktree,
# --git-common-dir points at the main repo's .git, so its parent is the checkout
# that holds .venv and .env. This is what makes these targets work from any
# worktree on any machine, whatever the parent directory happens to be called.
MAIN_CHECKOUT := $(shell d=$$(git rev-parse --git-common-dir 2>/dev/null); \
	[ -n "$$d" ] && cd "$$(dirname "$$d")" 2>/dev/null && pwd)

# Put a venv's bin dir on PATH so pre-commit and pytest resolve regardless of
# where the user's virtualenv lives. Checked in order; first hit wins. The
# git-derived path leads because the hardcoded ones below are a guess about
# directory layout that has already been wrong (~/emdash/repositories/... is not
# ~/emdash-projects/...), and a guess that misses makes `make commit` fail with
# "no pre-commit found" on a machine where the venv is sitting right there.
#
# Override if yours is elsewhere:
#   make commit VENV_BIN=/path/to/venv/bin
VENV_SEARCH_PATHS := ./.venv/bin $(MAIN_CHECKOUT)/.venv/bin \
	$(HOME)/emdash-projects/connect-labs/.venv/bin $(HOME)/venvs/commcare-labs/bin

VENV_BIN ?= $(shell \
	for p in $(VENV_SEARCH_PATHS); do \
		if [ -x "$$p/pre-commit" ]; then echo "$$p"; break; fi; \
	done)

commit:
	@if [ -z "$(VENV_BIN)" ] || [ ! -x "$(VENV_BIN)/pre-commit" ]; then \
		echo "error: no pre-commit found at VENV_BIN=$(VENV_BIN)" >&2; \
		echo "Tried: $(VENV_SEARCH_PATHS)" >&2; \
		echo "Override with: make commit VENV_BIN=/path/to/venv/bin" >&2; \
		exit 1; \
	fi
	PATH="$(VENV_BIN):$$PATH" git commit

# Django's GIS backend needs to be pointed at the GDAL/GEOS shared libraries on
# macOS (config/settings/test.py reads them for Darwin only). They are not in
# .env, so pytest from a fresh shell dies with "Set the GDAL_LIBRARY_PATH
# environment variable" — an error about the test settings that is really about
# the machine. Resolved from the usual Homebrew prefixes; ?= so an existing
# export or a `make test GDAL_LIBRARY_PATH=...` still wins.
ifeq ($(shell uname -s),Darwin)
GDAL_LIBRARY_PATH ?= $(firstword $(wildcard /opt/homebrew/lib/libgdal.dylib /usr/local/lib/libgdal.dylib))
GEOS_LIBRARY_PATH ?= $(firstword $(wildcard /opt/homebrew/lib/libgeos_c.dylib /usr/local/lib/libgeos_c.dylib))
export GDAL_LIBRARY_PATH
export GEOS_LIBRARY_PATH
endif

# Run pytest the way CI does, from anywhere — including a worktree, where none of
# the three things pytest needs are present by default: the venv lives in the main
# checkout, `.env` is untracked and so does not exist here at all (settings read it
# from BASE_DIR, which is this worktree), and the GIS library paths are unset.
#
# Pass pytest arguments through ARGS:
#   make test
#   make test ARGS="connect_labs/audit -q"
#   make test ARGS="-k uvicorn_worker"
ARGS ?= connect_labs/

test:
	@if [ -z "$(VENV_BIN)" ] || [ ! -x "$(VENV_BIN)/pytest" ]; then \
		echo "error: no pytest found at VENV_BIN=$(VENV_BIN)" >&2; \
		echo "Tried: $(VENV_SEARCH_PATHS)" >&2; \
		echo "Override with: make test VENV_BIN=/path/to/venv/bin" >&2; \
		exit 1; \
	fi
	@if [ ! -e .env ]; then \
		if [ -n "$(MAIN_CHECKOUT)" ] && [ -f "$(MAIN_CHECKOUT)/.env" ] && [ "$(MAIN_CHECKOUT)" != "$$(pwd)" ]; then \
			ln -s "$(MAIN_CHECKOUT)/.env" .env && \
			echo "note: linked .env -> $(MAIN_CHECKOUT)/.env (untracked, and a link so it cannot go stale)"; \
		else \
			echo "error: no .env here and none found in the main checkout" >&2; \
			echo "Copy .env.tpl to .env and fill it in." >&2; \
			exit 1; \
		fi; \
	fi
	PATH="$(VENV_BIN):$$PATH" $(VENV_BIN)/pytest $(ARGS)

# Run manage.py with everything a worktree lacks — the same three fixes as
# `test` (venv on PATH, .env linked from the main checkout, GIS library paths on
# macOS). Without it, any management command in a worktree dies with an
# ImproperlyConfigured or a GDAL error that names neither the venv nor the
# missing .env.
#
#   make manage CMD="migrate"
#   make manage CMD="load_africa_boundaries --iso NGA"
CMD ?= help

manage:
	@if [ -z "$(VENV_BIN)" ] || [ ! -x "$(VENV_BIN)/python" ]; then \
		echo "error: no python found at VENV_BIN=$(VENV_BIN)" >&2; \
		echo "Tried: $(VENV_SEARCH_PATHS)" >&2; \
		exit 1; \
	fi
	@if [ ! -e .env ]; then \
		if [ -n "$(MAIN_CHECKOUT)" ] && [ -f "$(MAIN_CHECKOUT)/.env" ] && [ "$(MAIN_CHECKOUT)" != "$$(pwd)" ]; then \
			ln -s "$(MAIN_CHECKOUT)/.env" .env && \
			echo "note: linked .env -> $(MAIN_CHECKOUT)/.env"; \
		else \
			echo "error: no .env here and none found in the main checkout" >&2; \
			exit 1; \
		fi; \
	fi
	PATH="$(VENV_BIN):$$PATH" $(VENV_BIN)/python manage.py $(CMD)
