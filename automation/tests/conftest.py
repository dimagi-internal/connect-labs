# Automation scripts don't use Django — disable the pytest-django plugin for this directory.
collect_ignore_glob = []


def pytest_configure(config):
    pass
