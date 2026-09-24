"""The OES demo runner: what it actually ships to the labs worker.

`ensure_demo.py` builds one shell command and hands it to `aws ecs
execute-command`. Nothing between here and the deployed worker reads it, so
every defect in it is discovered at the far end, half a seed in, as a shell
error or a traceback in a session log. These are the three that have already
happened or are one edit away, checked here instead.

No partner name, quantity or price appears here: the runner is handed a
placeholder folder id and never reaches Drive.
"""

import base64
import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_ENSURE_DEMO_PATH = _ROOT / "scripts" / "walkthroughs" / "oes-demo" / "ensure_demo.py"


def _load_runner():
    """By path: `oes-demo` has a hyphen, so it cannot be imported."""
    spec = importlib.util.spec_from_file_location("oes_demo_ensure_demo", _ENSURE_DEMO_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _driver_of(command):
    """The payload back out of the command, as the worker would decode it."""
    encoded = command.split("b64decode('")[1].split("')")[0]
    return base64.b64decode(encoded).decode()


def test_the_payload_is_valid_python():
    """A syntax error in the driver is a traceback on the worker, not here.

    The driver is written as a string, so nothing compiles it on the way
    out -- `python -m compileall`, the linters and every other test in this
    repo see a string literal. The first thing that reads it as code is the
    deployed worker, after the ECS session has opened.
    """
    command = _load_runner().build_command("placeholder-folder", "placeholder.json")
    compile(_driver_of(command), "driver", "exec")


def test_the_payload_fits_the_command_line_it_travels_on():
    """ECS exec puts the whole command on the container's argv.

    Measured against the labs worker: 120,101 characters ran, 131,101 came
    back "fork/exec /usr/local/bin/python: argument list too long" and
    173,441 was refused by the API itself. So this route has a hard ceiling
    a growing seeder will reach, and it fails as a shell message that reads
    like anything but "the payload got too big".
    """
    runner = _load_runner()
    command = runner.build_command("placeholder-folder", "placeholder.json")
    assert len(command) <= runner.MAX_COMMAND, (
        f"the payload is {len(command):,} characters, past the {runner.MAX_COMMAND:,} this route "
        "carries: it needs to reach the worker some other way than on a command line"
    )


def test_the_drive_loader_travels_with_the_payload_rather_than_being_imported():
    """`connect_labs.labs.synthetic.seed_data` is NOT on the deployed labs.

    It is this branch's own module, and the branch is not deployed, so a
    seeder that imports it passes every test here and dies on the worker with
    `ModuleNotFoundError` -- which is exactly what happened. The payload
    therefore carries that module's source and exec's it.

    Asserting the loader's source is IN the payload, rather than that the
    payload merely mentions `load_seed_data`, is the difference: an import
    statement mentions it too.
    """
    runner = _load_runner()
    driver = _driver_of(runner.build_command("placeholder-folder", "placeholder.json"))
    loader_source = runner.LOADER.read_text()

    shipped = [base64.b64decode(chunk.split('"')[0]).decode() for chunk in driver.split('b64decode("')[1:]]
    assert loader_source in shipped, "the Drive loader's own source is not in the payload"
    # And it is shipped as its own compilation unit, so its `from __future__`
    # import is still the first statement of something.
    compile(loader_source, "seed_data.py", "exec")
