.PHONY: commit

# Put a venv's bin dir on PATH so the pre-commit hook resolves regardless
# of where the user's virtualenv lives. Checked in order; first hit wins.
#
# Override if yours is elsewhere:
#   make commit VENV_BIN=/path/to/venv/bin
VENV_SEARCH_PATHS := ./.venv/bin $(HOME)/emdash-projects/connect-labs/.venv/bin $(HOME)/venvs/commcare-labs/bin

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
