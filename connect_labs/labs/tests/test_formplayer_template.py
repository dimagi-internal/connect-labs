"""Static checks on the labs formplayer: infra/labs-formplayer.yml and the formplayer/ overlay.

The properties pinned here are only enforced at apply time, or not at all:

- never public: no load balancer, and the only ingress is 8080 from the labs task security group.
  The management port (8081: /health, /info, /metrics) has no rule anywhere;
- one task, stop-then-start: formplayer keeps per-user SQLite state and expects user affinity, so
  a second task (or a rolling deploy's overlap) would split or corrupt it;
- stateful resources (ECR, EFS) survive a stack delete;
- the web tier's CONNECT_WEB_FORMPLAYER_URL is the address the stack registers;
- the pin script (formplayer/DEPLOY.md step 7) adds Connect Web's secrets to web.json without
  touching anything else in it, and never silently re-points one.
"""

import importlib.util
import json
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
TEMPLATE_PATH = ROOT / "infra" / "labs-formplayer.yml"
WEB_JSON = ROOT / "deploy" / "task-definitions" / "web.json"
OVERLAY = ROOT / "formplayer"


class _CfnLoader(yaml.SafeLoader):
    """SafeLoader that keeps CloudFormation short-form intrinsics as {"!Tag": value}."""


def _intrinsic(loader, suffix, node):
    if isinstance(node, yaml.ScalarNode):
        value = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node, deep=True)
    else:
        value = loader.construct_mapping(node, deep=True)
    return {f"!{suffix}": value}


_CfnLoader.add_multi_constructor("!", _intrinsic)


@pytest.fixture(scope="module")
def doc():
    return yaml.load(TEMPLATE_PATH.read_text(), Loader=_CfnLoader)


def _of_type(doc, cfn_type):
    return {k: r for k, r in doc["Resources"].items() if r["Type"] == cfn_type}


def _container(doc):
    (container,) = doc["Resources"]["TaskDefinition"]["Properties"]["ContainerDefinitions"]
    return container


def test_never_public(doc):
    resources = doc["Resources"]
    assert not any(r["Type"].startswith("AWS::ElasticLoadBalancingV2::") for r in resources.values())
    assert "LoadBalancers" not in resources["Service"]["Properties"]

    ingress = resources["ServiceSecurityGroup"]["Properties"]["SecurityGroupIngress"]
    assert ingress == [
        {
            "IpProtocol": "tcp",
            "FromPort": 8080,
            "ToPort": 8080,
            "SourceSecurityGroupId": {"!Ref": "LabsTaskSecurityGroupId"},
            "Description": "formplayer API from the labs web tier",
        }
    ]
    assert not _of_type(doc, "AWS::EC2::SecurityGroupIngress")
    for sg in _of_type(doc, "AWS::EC2::SecurityGroup").values():
        for rule in sg["Properties"].get("SecurityGroupIngress", []):
            assert not rule["FromPort"] <= 8081 <= rule["ToPort"], rule
    assert [p["ContainerPort"] for p in _container(doc)["PortMappings"]] == [8080]


def test_one_task_stop_then_start(doc):
    assert doc["Parameters"]["DesiredCount"]["AllowedValues"] == [0, 1]
    deployment = doc["Resources"]["Service"]["Properties"]["DeploymentConfiguration"]
    assert deployment["MinimumHealthyPercent"] == 0
    assert deployment["MaximumPercent"] == 100


def test_stateful_resources_are_retained(doc):
    for logical_id in ("Repository", "FileSystem"):
        resource = doc["Resources"][logical_id]
        assert resource.get("DeletionPolicy") == "Retain", logical_id
        assert resource.get("UpdateReplacePolicy") == "Retain", logical_id


def test_labs_mode_on_and_secrets_are_references(doc):
    container = _container(doc)
    env = {e["Name"]: e["Value"] for e in container["Environment"]}
    assert env["LABS_ENABLED"] == "true"
    assert env["SERVER_PORT"] == "8080" and env["MANAGEMENT_SERVER_PORT"] == "8081"
    assert env["COMMCARE_HOST"] == {"!Ref": "CommcareHost"}
    secret_names = {s["Name"] for s in container["Secrets"]}
    assert secret_names == {"LABS_AUTH_KEY", "AUTH_KEY", "POSTGRESQL_PASSWROD"}
    assert not secret_names & set(env)
    for secret in container["Secrets"]:
        assert set(secret["ValueFrom"]) == {"!Ref"}


def test_redis_database_is_never_labs_own(doc):
    # labs' Django cache and Celery broker are database 0 (web.json REDIS_URL / CELERY_BROKER_URL).
    assert doc["Parameters"]["RedisDatabase"]["MinValue"] >= 1
    web_env = {
        e["name"]: e["value"] for e in json.loads(WEB_JSON.read_text())["containerDefinitions"][0]["environment"]
    }
    assert web_env["REDIS_URL"].endswith("/0")


def test_sqlite_data_dir_is_on_efs(doc):
    container = _container(doc)
    (mount,) = container["MountPoints"]
    assert mount["ContainerPath"] == "/application/dbs"
    (volume,) = doc["Resources"]["TaskDefinition"]["Properties"]["Volumes"]
    assert volume["Name"] == mount["SourceVolume"]
    assert volume["EFSVolumeConfiguration"]["TransitEncryption"] == "ENABLED"


def test_web_tier_points_at_the_registered_name(doc):
    url = doc["Outputs"]["FormplayerUrl"]["Value"]
    namespace = doc["Resources"]["Namespace"]["Properties"]["Name"]
    service = doc["Resources"]["DiscoveryService"]["Properties"]["Name"]
    assert url == f"http://{service}.{namespace}:8080"
    web_env = {
        e["name"]: e["value"] for e in json.loads(WEB_JSON.read_text())["containerDefinitions"][0]["environment"]
    }
    assert web_env["CONNECT_WEB_FORMPLAYER_URL"] == url


def test_overlay_is_pinned_and_patched():
    upstream = dict(
        line.split("=", 1) for line in (OVERLAY / "UPSTREAM").read_text().splitlines() if line and line[0] != "#"
    )
    assert re.fullmatch(r"[0-9a-f]{40}", upstream["FORMPLAYER_SHA"])
    assert upstream["FORMPLAYER_REPO"] == "https://github.com/dimagi/formplayer.git"
    patches = sorted(p.name for p in (OVERLAY / "patches").glob("*.patch"))
    assert patches and patches[0].startswith("0001-")
    assert [p[:4] for p in patches] == [f"{i:04d}" for i in range(1, len(patches) + 1)]


def _pin_module():
    spec = importlib.util.spec_from_file_location("pin_web_secrets", OVERLAY / "pin_web_secrets.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _without_pins(text: str, names) -> str:
    """web.json as it was before the pin script ran: drop each pinned four-line `secrets[]` entry."""
    lines = text.splitlines(keepends=True)
    out, i = [], 0
    while i < len(lines):
        if lines[i].strip() == "{" and i + 1 < len(lines) and any(f'"name": "{n}"' in lines[i + 1] for n in names):
            i += 4
            continue
        out.append(lines[i])
        i += 1
    return "".join(out)


def test_pin_script_inserts_without_reformatting():
    pin = _pin_module()
    arns = {
        env: f"arn:aws:secretsmanager:us-east-1:123456789012:secret:{name}-AbC123" for env, name in pin.PINS.items()
    }
    # The real web.json is pinned now (DEPLOY.md step 7 ran); test the insertion on the unpinned form.
    text = _without_pins(WEB_JSON.read_text(), pin.PINS)
    assert not any(f'"{n}"' in text for n in pin.PINS)
    result = pin.pin(text, arns)
    assert pin.pin(result, arns) == result  # idempotent
    removed = [line for line in text.splitlines() if line not in result.splitlines()]
    assert removed == []
    secrets = {s["name"]: s["valueFrom"] for s in json.loads(result)["containerDefinitions"][0]["secrets"]}
    assert all(secrets[k] == v for k, v in arns.items())
    with pytest.raises(SystemExit):
        pin.pin(result, {"CONNECT_WEB_FORMPLAYER_KEY": arns["CONNECT_WEB_FORMPLAYER_KEY"].replace("AbC123", "XyZ789")})


def test_web_json_pins_every_connect_web_secret_once():
    pin = _pin_module()
    secrets = [s["name"] for s in json.loads(WEB_JSON.read_text())["containerDefinitions"][0]["secrets"]]
    for name in pin.PINS:
        assert secrets.count(name) == 1, name
