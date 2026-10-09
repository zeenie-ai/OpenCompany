"""Contract tests for the AWS node (``awsAction``).

The node calls AWS through the official boto3 SDK; every SDK touch lives in
``nodes/aws/_sdk.py``. These tests replace its client factory with real
botocore clients wrapped in ``botocore.stub.Stubber`` (the SDK's own test
double): requests are still validated against the real API models, and
nothing touches the network.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import get_args

import botocore.session
import pytest
from botocore.response import StreamingBody
from botocore.stub import Stubber

from tests.nodes._mocks import patched_container

pytestmark = pytest.mark.node_contract

import nodes.aws as aws_pkg  # noqa: E402
from nodes.aws import _sdk, aws_action  # noqa: E402
from nodes.aws._credentials import AwsCredential  # noqa: E402
from services.node_registry import get_node_class  # noqa: E402
from services.plugin.base import NodeUserError  # noqa: E402
from services.plugin.credential import CREDENTIAL_REGISTRY  # noqa: E402
from services.ws_handler_registry import get_option_loader  # noqa: E402

PLUGIN_DIR = Path(aws_pkg.__file__).parent
SERVER_DIR = PLUGIN_DIR.parents[1]
SKILL_MD = SERVER_DIR / "skills" / "aws" / "aws-skill" / "SKILL.md"

KEYS = {"aws": "secret", "aws_access_key_id": "AKIDEXAMPLE", "aws_region": "eu-west-1"}
IDENTITY = {"UserId": "AIDAEXAMPLE", "Account": "123456789012", "Arn": "arn:aws:iam::123456789012:user/ops"}
LAUNCHED = dt.datetime(2026, 10, 9, 12, 0, tzinfo=dt.timezone.utc)


class StubbedClients:
    """Stands in for ``_sdk._client``: one stubbed client per service + region."""

    def __init__(self) -> None:
        self.stubbers: dict = {}
        self.calls: list = []

    def stub(self, service: str, region: str = "eu-west-1") -> Stubber:
        if (service, region) not in self.stubbers:
            client = botocore.session.get_session().create_client(
                service, region_name=region, aws_access_key_id="x", aws_secret_access_key="y"
            )
            stubber = Stubber(client)
            stubber.activate()
            self.stubbers[(service, region)] = stubber
        return self.stubbers[(service, region)]

    def client(self, session, service: str, region: str):
        self.calls.append((service, region))
        return self.stub(service, region).client


@pytest.fixture
def clients(monkeypatch):
    stubs = StubbedClients()
    monkeypatch.setattr(_sdk, "_client", stubs.client)
    yield stubs
    for stubber in stubs.stubbers.values():
        stubber.assert_no_pending_responses()


async def _execute(harness, params, *, keys=KEYS, **kwargs):
    with patched_container(auth_api_keys=keys):
        return await harness.execute("awsAction", params, **kwargs)


def _instance(instance_id: str, name: str, state: str = "running") -> dict:
    return {
        "InstanceId": instance_id,
        "InstanceType": "t3.micro",
        "State": {"Code": 16, "Name": state},
        "Tags": [{"Key": "Name", "Value": name}],
        "PublicIpAddress": "203.0.113.10",
        "PrivateIpAddress": "172.31.0.10",
        "Placement": {"AvailabilityZone": "eu-west-1a"},
        "LaunchTime": LAUNCHED,
        "ImageId": "ami-0abc",
        "KeyName": "ops",
    }


# ============================================================================
# Registration
# ============================================================================


class TestRegistration:
    def test_class_attributes(self):
        cls = get_node_class("awsAction")
        assert cls is not None, "awsAction is not registered"
        assert cls.tool_name == "aws_action"
        assert tuple(cls.group) == ("deployment", "tool")
        assert cls.usable_as_tool is True
        assert AwsCredential in tuple(cls.credentials)
        assert cls.annotations["destructive"] is True

    def test_both_canvas_handles_stay_visible(self):
        cls = get_node_class("awsAction")
        assert cls.hide_input_handle is False
        assert cls.hide_output_handle is False
        assert {h["name"] for h in cls.handles} == {"input-main", "output-main"}

    def test_option_loaders_and_credential_registered(self):
        assert get_option_loader("awsServices") is not None
        assert get_option_loader("awsOperations") is not None
        assert CREDENTIAL_REGISTRY["aws"] is AwsCredential

    def test_tool_schema_is_flat(self):
        from services.plugin.tool import inline_schema_refs

        cls = get_node_class("awsAction")
        schema = inline_schema_refs(cls.Params.model_json_schema())
        dumped = json.dumps(schema)
        assert "$defs" not in dumped
        assert '"$ref"' not in dumped

    def test_operations_match_the_literal(self):
        cls = get_node_class("awsAction")
        declared = set(get_args(cls.Params.model_fields["operation"].annotation))
        assert declared == set(aws_action._OPERATIONS)
        assert cls.Params().operation == "whoami"

    def test_reserved_field_names_absent(self):
        # ParameterRenderer keys magic on these literal names.
        cls = get_node_class("awsAction")
        assert not {"model", "api_key", "parameters", "action", "service_id", "session_id"} & set(cls.Params.model_fields)


# ============================================================================
# Identity + credentials at run time
# ============================================================================


class TestWhoami:
    async def test_uses_the_default_region_from_the_credential(self, harness, clients):
        clients.stub("sts").add_response("get_caller_identity", dict(IDENTITY), {})

        result = await _execute(harness, {"operation": "whoami"})

        harness.assert_envelope(result, success=True)
        payload = result["result"]
        assert payload["operation"] == "whoami"
        assert payload["region"] == "eu-west-1"
        assert payload["result"] == IDENTITY
        assert clients.calls == [("sts", "eu-west-1")]

    async def test_region_parameter_overrides_the_default(self, harness, clients):
        clients.stub("sts", "us-west-2").add_response("get_caller_identity", dict(IDENTITY), {})

        result = await _execute(harness, {"operation": "whoami", "region": "us-west-2"})

        harness.assert_envelope(result, success=True)
        assert result["result"]["region"] == "us-west-2"
        assert clients.calls == [("sts", "us-west-2")]

    async def test_without_a_saved_region_uses_us_east_1(self, harness, clients):
        clients.stub("sts", "us-east-1").add_response("get_caller_identity", dict(IDENTITY), {})
        keys = {k: v for k, v in KEYS.items() if k != "aws_region"}

        result = await _execute(harness, {"operation": "whoami"}, keys=keys)

        harness.assert_envelope(result, success=True)
        assert result["result"]["region"] == "us-east-1"

    @pytest.mark.parametrize("keys", [{}, {"aws": "secret"}])
    async def test_missing_key_parts_point_at_the_modal(self, harness, clients, keys):
        result = await _execute(harness, {"operation": "whoami"}, keys=keys)

        harness.assert_envelope(result, success=False)
        assert result["error_type"] == "PermissionDeniedError"
        assert clients.calls == []

    async def test_aws_errors_carry_code_and_hint(self, harness, clients):
        clients.stub("sts").add_client_error(
            "get_caller_identity",
            service_error_code="InvalidClientTokenId",
            service_message="The security token included in the request is invalid.",
            http_status_code=403,
        )

        result = await _execute(harness, {"operation": "whoami"})

        harness.assert_envelope(result, success=False)
        assert result["error_type"] == "NodeUserError"
        assert "InvalidClientTokenId" in result["error"]
        assert "Credentials -> AWS" in result["error"]


# ============================================================================
# EC2
# ============================================================================


class TestEc2:
    async def test_list_returns_one_row_per_instance(self, harness, clients):
        clients.stub("ec2").add_response(
            "describe_instances",
            {"Reservations": [{"Instances": [_instance("i-1", "web-1"), _instance("i-2", "web-2", "stopped")]}]},
            {},
        )

        result = await _execute(harness, {"operation": "ec2_instances_list"})

        harness.assert_envelope(result, success=True)
        payload = result["result"]
        assert payload["count"] == 2
        assert payload["truncated"] is False
        assert payload["result"][0] == {
            "InstanceId": "i-1",
            "Name": "web-1",
            "State": "running",
            "InstanceType": "t3.micro",
            "PublicIpAddress": "203.0.113.10",
            "PrivateIpAddress": "172.31.0.10",
            "AvailabilityZone": "eu-west-1a",
            "LaunchTime": LAUNCHED.isoformat(),
            "ImageId": "ami-0abc",
            "KeyName": "ops",
        }

    async def test_state_filter_is_sent(self, harness, clients):
        clients.stub("ec2").add_response(
            "describe_instances",
            {"Reservations": []},
            {"Filters": [{"Name": "instance-state-name", "Values": ["running"]}]},
        )

        result = await _execute(harness, {"operation": "ec2_instances_list", "instance_state": "running"})

        harness.assert_envelope(result, success=True)
        assert result["result"]["result"] == []
        assert result["result"]["count"] == 0

    async def test_limit_marks_truncation(self, harness, clients):
        clients.stub("ec2").add_response(
            "describe_instances",
            {"Reservations": [{"Instances": [_instance("i-1", "a"), _instance("i-2", "b")]}]},
            {},
        )

        result = await _execute(harness, {"operation": "ec2_instances_list", "limit": 1})

        assert result["result"]["count"] == 1
        assert result["result"]["truncated"] is True

    async def test_describe_returns_the_full_instance(self, harness, clients):
        clients.stub("ec2").add_response(
            "describe_instances",
            {"Reservations": [{"Instances": [_instance("i-1", "web-1")]}]},
            {"InstanceIds": ["i-1"]},
        )

        result = await _execute(harness, {"operation": "ec2_instance_describe", "instance_id": "i-1"})

        harness.assert_envelope(result, success=True)
        instance = result["result"]["result"]
        assert instance["InstanceId"] == "i-1"
        assert instance["Tags"] == [{"Key": "Name", "Value": "web-1"}]
        assert instance["LaunchTime"] == LAUNCHED.isoformat()

    @pytest.mark.parametrize(
        ("operation", "method", "key"),
        [
            ("ec2_instance_start", "start_instances", "StartingInstances"),
            ("ec2_instance_stop", "stop_instances", "StoppingInstances"),
        ],
    )
    async def test_start_and_stop(self, harness, clients, operation, method, key):
        response = {
            key: [{"InstanceId": "i-1", "CurrentState": {"Code": 0, "Name": "pending"}, "PreviousState": {"Code": 80, "Name": "stopped"}}]
        }
        clients.stub("ec2").add_response(method, response, {"InstanceIds": ["i-1"]})

        result = await _execute(harness, {"operation": operation, "instance_id": "i-1"})

        harness.assert_envelope(result, success=True)
        assert result["result"]["result"] == response

    @pytest.mark.parametrize("operation", ["ec2_instance_describe", "ec2_instance_start", "ec2_instance_stop"])
    async def test_instance_id_is_required_before_any_call(self, harness, clients, operation):
        result = await _execute(harness, {"operation": operation})

        harness.assert_envelope(result, success=False)
        assert result["error_type"] == "NodeUserError"
        assert "instance_id" in result["error"]
        assert clients.calls == []

    async def test_permission_errors_name_the_cause(self, harness, clients):
        clients.stub("ec2").add_client_error(
            "start_instances",
            service_error_code="UnauthorizedOperation",
            service_message="You are not authorized to perform this operation.",
            http_status_code=403,
        )

        result = await _execute(harness, {"operation": "ec2_instance_start", "instance_id": "i-1"})

        assert result["error_type"] == "NodeUserError"
        assert "UnauthorizedOperation" in result["error"]
        assert "lacks this permission" in result["error"]


# ============================================================================
# S3
# ============================================================================


class TestS3:
    async def test_list_buckets_without_a_bucket(self, harness, clients):
        clients.stub("s3").add_response(
            "list_buckets", {"Buckets": [{"Name": "alpha", "CreationDate": LAUNCHED}], "Owner": {"ID": "abc"}}, {}
        )

        result = await _execute(harness, {"operation": "s3_list"})

        harness.assert_envelope(result, success=True)
        assert result["result"]["result"] == [{"Name": "alpha", "CreationDate": LAUNCHED.isoformat()}]

    async def test_list_objects_under_a_prefix(self, harness, clients):
        clients.stub("s3").add_response(
            "list_objects_v2",
            {
                "IsTruncated": False,
                "Contents": [{"Key": "reports/q3.pdf", "Size": 12, "LastModified": LAUNCHED, "StorageClass": "STANDARD"}],
            },
            {"Bucket": "alpha", "Prefix": "reports/"},
        )

        result = await _execute(harness, {"operation": "s3_list", "bucket": "alpha", "prefix": "reports/"})

        harness.assert_envelope(result, success=True)
        assert result["result"]["result"] == [
            {"Key": "reports/q3.pdf", "Size": 12, "LastModified": LAUNCHED.isoformat(), "StorageClass": "STANDARD"}
        ]

    async def test_upload_reads_a_workspace_file(self, harness, clients, tmp_path):
        (tmp_path / "reports").mkdir()
        (tmp_path / "reports" / "q3.txt").write_bytes(b"hello")
        clients.stub("s3").add_response(
            "put_object",
            {"ETag": '"etag"'},
            {"Bucket": "alpha", "Key": "q3.txt", "Body": b"hello", "ContentType": "text/plain"},
        )

        result = await _execute(
            harness,
            {"operation": "s3_upload", "bucket": "alpha", "file": "reports/q3.txt"},
            context=harness.build_context(workspace_dir=str(tmp_path)),
        )

        harness.assert_envelope(result, success=True)
        assert result["result"]["result"] == {"Bucket": "alpha", "Key": "q3.txt", "Size": 5, "ETag": '"etag"'}

    async def test_upload_refuses_paths_outside_the_workspace(self, harness, clients, tmp_path):
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (tmp_path / "secret.txt").write_text("do not upload", encoding="utf-8")

        result = await _execute(
            harness,
            {"operation": "s3_upload", "bucket": "alpha", "file": "../secret.txt"},
            context=harness.build_context(workspace_dir=str(workspace)),
        )

        harness.assert_envelope(result, success=False)
        assert result["error_type"] == "NodeUserError"
        assert "outside" in result["error"]
        assert clients.calls == []

    async def test_download_returns_a_file_reference(self, harness, clients, tmp_path):
        clients.stub("s3").add_response(
            "get_object",
            {"Body": StreamingBody(io.BytesIO(b"hello"), 5), "ContentLength": 5, "ContentType": "text/plain"},
            {"Bucket": "alpha", "Key": "reports/q3.txt"},
        )

        result = await _execute(
            harness,
            {"operation": "s3_download", "bucket": "alpha", "key": "reports/q3.txt"},
            context=harness.build_context(workspace_dir=str(tmp_path)),
        )

        harness.assert_envelope(result, success=True)
        payload = result["result"]
        assert payload["result"] == {"Bucket": "alpha", "Key": "reports/q3.txt", "Size": 5, "ContentType": "text/plain"}
        ref = payload["file"]
        assert ref["kind"] == "file"
        assert ref["path"].startswith("media/")
        assert (tmp_path / ref["path"]).read_bytes() == b"hello"

    async def test_download_refuses_oversize_objects(self, harness, clients, tmp_path, monkeypatch):
        monkeypatch.setattr(aws_action, "MEDIA_MAX_READ_BYTES", 4)
        clients.stub("s3").add_response(
            "get_object",
            {"Body": StreamingBody(io.BytesIO(b"hello"), 5), "ContentLength": 5},
            {"Bucket": "alpha", "Key": "big.bin"},
        )

        result = await _execute(
            harness,
            {"operation": "s3_download", "bucket": "alpha", "key": "big.bin"},
            context=harness.build_context(workspace_dir=str(tmp_path)),
        )

        harness.assert_envelope(result, success=False)
        assert "limit" in result["error"]
        assert not (tmp_path / "media").exists()

    async def test_delete(self, harness, clients):
        clients.stub("s3").add_response("delete_object", {}, {"Bucket": "alpha", "Key": "old.txt"})

        result = await _execute(harness, {"operation": "s3_delete", "bucket": "alpha", "key": "old.txt"})

        harness.assert_envelope(result, success=True)
        assert result["result"]["result"] == {"Bucket": "alpha", "Key": "old.txt"}

    @pytest.mark.parametrize(
        ("params", "missing"),
        [
            ({"operation": "s3_upload", "file": "a.txt"}, "bucket"),
            ({"operation": "s3_upload", "bucket": "alpha"}, "file"),
            ({"operation": "s3_download", "bucket": "alpha"}, "key"),
            ({"operation": "s3_delete", "key": "a.txt"}, "bucket"),
        ],
    )
    async def test_required_fields(self, harness, clients, params, missing):
        result = await _execute(harness, params)

        harness.assert_envelope(result, success=False)
        assert result["error_type"] == "NodeUserError"
        assert missing in result["error"]
        assert clients.calls == []


# ============================================================================
# call + list_operations
# ============================================================================


class TestCall:
    async def test_api_name_spelling_resolves_to_the_boto3_method(self, harness, clients):
        clients.stub("ec2").add_response(
            "describe_vpcs",
            {"Vpcs": [{"VpcId": "vpc-1", "IsDefault": True}]},
            {"Filters": [{"Name": "is-default", "Values": ["true"]}]},
        )

        result = await _execute(
            harness,
            {
                "operation": "call",
                "service": "ec2",
                "api_operation": "DescribeVpcs",
                "request": {"Filters": [{"Name": "is-default", "Values": ["true"]}]},
            },
        )

        harness.assert_envelope(result, success=True)
        payload = result["result"]
        assert payload["service"] == "ec2"
        assert payload["api_operation"] == "describe_vpcs"
        assert payload["result"] == {"Vpcs": [{"VpcId": "vpc-1", "IsDefault": True}]}

    async def test_request_as_json_string(self, harness, clients):
        clients.stub("ec2").add_response("describe_vpcs", {"Vpcs": []}, {"VpcIds": ["vpc-1"]})

        result = await _execute(
            harness,
            {"operation": "call", "service": "ec2", "api_operation": "describe_vpcs", "request": '{"VpcIds": ["vpc-1"]}'},
        )

        harness.assert_envelope(result, success=True)

    async def test_blank_request_from_the_panel(self, harness, clients):
        clients.stub("sts").add_response("get_caller_identity", dict(IDENTITY), {})

        result = await _execute(
            harness, {"operation": "call", "service": "sts", "api_operation": "get_caller_identity", "request": ""}
        )

        harness.assert_envelope(result, success=True)

    async def test_unknown_operation_fails_before_credentials(self, harness, clients):
        result = await _execute(
            harness, {"operation": "call", "service": "ec2", "api_operation": "describe_vpc"}, keys={}
        )

        harness.assert_envelope(result, success=False)
        assert result["error_type"] == "NodeUserError"
        assert "describe_vpcs" in result["error"]
        assert clients.calls == []

    async def test_unknown_service_suggests_close_names(self, harness, clients):
        result = await _execute(harness, {"operation": "call", "service": "ecs2", "api_operation": "x"})

        assert result["error_type"] == "NodeUserError"
        assert "ec2" in result["error"]

    async def test_wrong_parameter_is_rejected_before_sending(self, harness, monkeypatch):
        # A real, unstubbed client: botocore validates the parameters before
        # building any request (Stubber would check its queue first).
        client = botocore.session.get_session().create_client(
            "ec2", region_name="eu-west-1", aws_access_key_id="x", aws_secret_access_key="y"
        )
        monkeypatch.setattr(_sdk, "_client", lambda session, service, region: client)

        result = await _execute(
            harness,
            {"operation": "call", "service": "ec2", "api_operation": "describe_vpcs", "request": {"Bogus": 1}},
        )

        harness.assert_envelope(result, success=False)
        assert result["error_type"] == "NodeUserError"
        assert "Bogus" in result["error"]

    async def test_streaming_payload_is_read_inline(self, harness, clients):
        clients.stub("lambda").add_response(
            "invoke",
            {"StatusCode": 200, "Payload": StreamingBody(io.BytesIO(b'{"ok": true}'), 12)},
            {"FunctionName": "fn"},
        )

        result = await _execute(
            harness, {"operation": "call", "service": "lambda", "api_operation": "invoke", "request": {"FunctionName": "fn"}}
        )

        harness.assert_envelope(result, success=True)
        assert result["result"]["result"] == {"StatusCode": 200, "Payload": '{"ok": true}'}


class TestListOperations:
    async def test_lists_services_without_credentials(self, harness):
        result = await _execute(harness, {"operation": "list_operations", "search": "ec2"}, keys={})

        harness.assert_envelope(result, success=True)
        assert "ec2" in result["result"]["result"]

    async def test_lists_one_services_operations(self, harness):
        result = await _execute(harness, {"operation": "list_operations", "service": "sts"}, keys={})

        harness.assert_envelope(result, success=True)
        rows = {row["operation"]: row for row in result["result"]["result"]}
        assert rows["get_caller_identity"]["api_name"] == "GetCallerIdentity"
        assert rows["assume_role"]["required"] == ["RoleArn", "RoleSessionName"]
        assert result["result"]["total"] == len(rows)

    async def test_operation_dropdown_loader(self):
        options = await get_option_loader("awsOperations")({"service": "sts"})
        assert {"value": "get_caller_identity", "label": "get_caller_identity"}.items() <= next(
            o for o in options if o["value"] == "get_caller_identity"
        ).items()
        assert await get_option_loader("awsOperations")({}) == []


# ============================================================================
# Shaping
# ============================================================================


class TestToPlain:
    def test_timestamps_and_blobs_print_like_the_aws_cli(self):
        assert _sdk.to_plain({"T": LAUNCHED, "B": b"\x00\x01", "L": [1]}) == {
            "T": LAUNCHED.isoformat(),
            "B": "AAE=",
            "L": [1],
        }

    def test_large_streaming_bodies_are_refused(self, monkeypatch):
        monkeypatch.setattr(_sdk, "_MAX_INLINE_BODY_BYTES", 4)
        with pytest.raises(NodeUserError, match="s3_download"):
            _sdk.to_plain({"Payload": StreamingBody(io.BytesIO(b"hello"), 5)})


# ============================================================================
# Credential
# ============================================================================


class TestCredentialProbe:
    async def test_valid_key_pair(self, clients):
        clients.stub("sts").add_response("get_caller_identity", dict(IDENTITY), {})

        with patched_container(auth_api_keys={"aws_access_key_id": "AKIDEXAMPLE", "aws_region": "eu-west-1"}):
            result = await AwsCredential._probe("secret")

        assert result.valid is True
        assert IDENTITY["Arn"] in result.message
        assert result.extra == {"arn": IDENTITY["Arn"], "account": IDENTITY["Account"]}

    async def test_missing_access_key_id_explains_the_order(self, clients):
        with patched_container(auth_api_keys={}):
            result = await AwsCredential._probe("secret")

        assert result.valid is False
        assert "Access Key ID" in result.message
        assert clients.calls == []

    async def test_rejected_key_pair(self, clients):
        clients.stub("sts", "us-east-1").add_client_error(
            "get_caller_identity", service_error_code="SignatureDoesNotMatch", http_status_code=403
        )

        with patched_container(auth_api_keys={"aws_access_key_id": "AKIDEXAMPLE"}):
            result = await AwsCredential._probe("wrong")

        assert result.valid is False
        assert "SignatureDoesNotMatch" in result.message


class TestNoEagerSdkImport:
    """Importing the plugin must never import boto3 (clean interpreter: the
    pytest process already loaded the SDK above)."""

    def _probe(self, code: str) -> str:
        result = subprocess.run([sys.executable, "-c", code], cwd=SERVER_DIR, capture_output=True, text=True, timeout=180)
        assert result.returncode == 0, f"probe subprocess failed (rc={result.returncode}):\n{result.stderr}"
        return result.stdout

    def test_plugin_registers_without_the_sdk(self):
        out = self._probe(
            "import sys\n"
            "sys.modules['boto3'] = None\n"
            "import nodes.aws\n"
            "from services.node_registry import get_node_class\n"
            "print('REGISTERED=' + str(get_node_class('awsAction') is not None))\n"
        )
        assert "REGISTERED=True" in out

    def test_plugin_import_does_not_load_the_sdk(self):
        out = self._probe("import sys\nimport nodes.aws\nprint('LEAKED=' + str('boto3' in sys.modules))\n")
        assert "LEAKED=False" in out


# ============================================================================
# Catalogue + assets
# ============================================================================


class TestCatalogueAndAssets:
    def test_credential_providers_entry(self):
        config = json.loads((SERVER_DIR / "config" / "credential_providers.json").read_text(encoding="utf-8"))
        entry = config["providers"]["aws"]
        assert entry["kind"] == "apiKey"
        assert entry["category"] == "deployment"
        assert entry["icon_ref"] == "/api/schemas/credentials/aws/icon"
        assert [f["key"] for f in entry["fields"]] == ["apiKey", "aws_access_key_id", "aws_region"]
        assert entry["fields"][0]["secret"] is True
        assert tuple(AwsCredential.extra_fields) == ("aws_access_key_id", "aws_region")

    def test_visuals_skill_binding(self):
        visuals = json.loads((SERVER_DIR / "nodes" / "visuals.json").read_text(encoding="utf-8"))
        assert visuals["awsAction"]["skill"] == "aws-skill"

    def test_tool_name_snapshot(self):
        snapshot = json.loads((SERVER_DIR / "tests" / "fixtures" / "tool_names_snapshot.json").read_text(encoding="utf-8"))
        assert snapshot["awsAction"] == "aws_action"

    def test_plugin_folder_assets(self):
        meta = json.loads((PLUGIN_DIR / "meta.json").read_text(encoding="utf-8"))
        assert re.fullmatch(r"#[0-9a-fA-F]{6}", meta["color"])
        assert "<svg" in (PLUGIN_DIR / "icon.svg").read_text(encoding="utf-8")
        assert AwsCredential.get_icon_path() == PLUGIN_DIR / "aws.svg"

    def test_skill_binds_the_tool(self):
        text = SKILL_MD.read_text(encoding="utf-8")
        frontmatter = text.split("---", 2)[1]
        assert "name: aws-skill" in frontmatter
        assert '"aws_action"' in frontmatter
