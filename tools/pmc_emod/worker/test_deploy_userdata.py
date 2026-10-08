"""The labs-emod stack's UserData default must be the base64 of deploy/aws/labs-emod-userdata.sh."""

import base64
import pathlib
import re

DEPLOY = pathlib.Path(__file__).resolve().parents[3] / "deploy" / "aws"


def test_userdata_default_matches_script():
    template = (DEPLOY / "labs-emod.cfn.yaml").read_text()
    m = re.search(r"""\n  UserData:\n(?:    .*\n)*?    Default: ["']([A-Za-z0-9+/=]*)["']""", template)
    assert m, "UserData parameter default not found"
    script = (DEPLOY / "labs-emod-userdata.sh").read_bytes()
    assert (
        base64.b64decode(m.group(1)) == script
    ), "re-encode: base64 < deploy/aws/labs-emod-userdata.sh | tr -d '\\n' into the UserData Default"


def test_userdata_fits_a_parameter():
    # CloudFormation caps a parameter value at 4096 bytes.
    script = (DEPLOY / "labs-emod-userdata.sh").read_bytes()
    assert len(base64.b64encode(script)) <= 4096
