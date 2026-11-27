"""Static checks on canopy's sealed role keys (infra/canopy-sealed-keys.yml) and the roles that trust them.

Each labs agent role is reached by its own IAM user, whose access key canopy-web stores only
sealed in a person's browser (canopy-web apps/agents/aws_sealed.py). Pinned, because a wider
user would turn a key canopy-web carries into one it could misuse:

- each user may assume exactly its one role, and only with a `canopy-agent-*` SourceIdentity;
- no access key is made in the stack (an output would hold the secret);
- each role's trust names its own user, in both the assume and the tag statements;
- labs-readwrite cannot change or re-key the users.
"""

from pathlib import Path

import yaml

INFRA = Path(__file__).resolve().parents[3] / "infra"
USERS = {"LabsReadUser": ("canopy-sealed-labs-read", "labs-jj-observe-readonly", "labs-observe-readonly.yml"),
         "LabsReadWriteUser": ("canopy-sealed-labs-readwrite", "labs-jj-agent-readwrite", "labs-agent-readwrite.yml"),
         "LabsConfigUser": ("canopy-sealed-labs-config", "labs-jj-agent-config", "labs-agent-config.yml")}


class _CfnLoader(yaml.SafeLoader):
    """Keeps a short-form intrinsic's argument (`!Sub 'x'` -> 'x', `!If [c, a, b]` -> [c, a, b])."""


def _intrinsic(loader, suffix, node):
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    return loader.construct_mapping(node, deep=True)


_CfnLoader.add_multi_constructor("!", _intrinsic)


def _doc(name):
    return yaml.load((INFRA / name).read_text(), Loader=_CfnLoader)


def _flat(value):
    if isinstance(value, list):
        for v in value:
            yield from _flat(v)
    elif isinstance(value, str):
        yield value


def test_each_user_may_only_assume_its_one_role_as_an_agent():
    resources = _doc("canopy-sealed-keys.yml")["Resources"]
    assert {k for k, v in resources.items() if v["Type"] == "AWS::IAM::AccessKey"} == set()
    for logical, (user, role, _) in USERS.items():
        props = resources[logical]["Properties"]
        assert props["UserName"] == user and props["Path"] == "/canopy-sealed/"
        assert "LoginProfile" not in props and "ManagedPolicyArns" not in props
        statements = [s for p in props["Policies"] for s in p["PolicyDocument"]["Statement"]]
        actions = {a for s in statements for a in ([s["Action"]] if isinstance(s["Action"], str) else s["Action"])}
        assert actions == {"sts:AssumeRole", "sts:SetSourceIdentity", "sts:TagSession"}
        for s in statements:
            assert s["Effect"] == "Allow" and s["Resource"].endswith(f":role/{role}")
            if "sts:AssumeRole" in s["Action"]:
                assert s["Condition"] == {"StringLike": {"sts:SourceIdentity": "canopy-agent-*"}}


def test_each_role_trusts_its_own_user():
    for user, role, template in USERS.values():
        roles = [r for r in _doc(template)["Resources"].values()
                 if r["Type"] == "AWS::IAM::Role" and "AssumeRolePolicyDocument" in r["Properties"]
                 and r["Properties"]["AssumeRolePolicyDocument"]["Statement"][0]["Principal"].get("AWS")]
        assert len(roles) == 1, template
        statements = roles[0]["Properties"]["AssumeRolePolicyDocument"]["Statement"]
        want = f"arn:${{AWS::Partition}}:iam::${{AWS::AccountId}}:user/canopy-sealed/{user}"
        for s in statements:
            assert want in _flat(s["Condition"]["ArnLike"]["aws:PrincipalArn"]), (template, s.get("Sid"))


def test_readwrite_cannot_change_the_sealed_users():
    deny = _doc("labs-agent-readwrite.yml")["Resources"]["ReadWriteDenyPolicy"]["Properties"]["PolicyDocument"]
    access = next(s for s in deny["Statement"] if s["Sid"] == "NoChangingAccessRoles")
    assert "arn:${AWS::Partition}:iam::${AWS::AccountId}:user/canopy-sealed/*" in access["Resource"]
    assert "iam:Get*" in access["NotAction"]
