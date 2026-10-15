# The repository-root conftest: loaded before any other, so a plugin named here
# is in place before the first test module is imported. `connect_labs` is not
# importable yet when addopts' `-p` plugins load, which is why this is not one.
pytest_plugins = ["connect_labs.testing.clock_shift", "connect_labs.testing.shard"]
