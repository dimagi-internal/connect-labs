.PHONY: commit

# Git commit with the main repo's venv on PATH so pre-commit hooks work in worktrees.
# The virtualenv lives at ~/emdash-projects/connect-labs/.venv, which is NOT
# present in worktree directories. This target ensures pre-commit can find its
# dependencies regardless of which worktree you're in.
commit:
	PATH="$(HOME)/emdash-projects/connect-labs/.venv/bin:$$PATH" git commit
