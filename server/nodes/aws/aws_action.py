"""AWS Action — typed core operations over the official boto3 SDK, plus a
passthrough to any AWS API operation.

Dual-purpose: a workflow node and the AI tool ``aws_action``. Auth is the IAM
access key pair saved in Credentials -> AWS (``_credentials.py``), passed
explicitly to every SDK client. Every SDK touch lives in ``_sdk.py``, imported
lazily. Results are AWS's own response fields, shaped the way the AWS CLI
prints them.
"""

from __future__ import annotations

import asyncio
import mimetypes
from pathlib import PurePosixPath
from typing import Any, Dict, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, model_validator

from services.media import MEDIA_MAX_READ_BYTES, coerce_file_param, write_media
from services.plugin import ActionNode, NodeContext, NodeUserError, Operation, TaskQueue, coerce_blank_params

from . import _sdk
from ._credentials import DEFAULT_REGION, REGION_FIELD, AwsCredential

_OPERATIONS = (
    "whoami",
    "ec2_instances_list",
    "ec2_instance_describe",
    "ec2_instance_start",
    "ec2_instance_stop",
    "s3_list",
    "s3_upload",
    "s3_download",
    "s3_delete",
    "call",
    "list_operations",
)


def _show(*ops: str) -> Dict[str, Any]:
    return {"displayOptions": {"show": {"operation": list(ops)}}}


_REGIONAL = _show(*(op for op in _OPERATIONS if op != "list_operations"))
_INSTANCE = _show("ec2_instance_describe", "ec2_instance_start", "ec2_instance_stop")
_INSTANCES_LIST = _show("ec2_instances_list")
_BUCKET = _show("s3_list", "s3_upload", "s3_download", "s3_delete")
_PREFIX = _show("s3_list")
_KEY = _show("s3_upload", "s3_download", "s3_delete")
_UPLOAD = _show("s3_upload")
_SERVICE = _show("call", "list_operations")
_CALL = _show("call")
_SEARCH = _show("list_operations")
_LIMIT = _show("ec2_instances_list", "s3_list", "list_operations")

# The AWS CLI's own --query idiom for an instance overview: one row per
# instance with the fields that identify and locate it.
_INSTANCE_SUMMARY = (
    "Reservations[].Instances[].{InstanceId: InstanceId, Name: Tags[?Key=='Name'] | [0].Value, "
    "State: State.Name, InstanceType: InstanceType, PublicIpAddress: PublicIpAddress, "
    "PrivateIpAddress: PrivateIpAddress, AvailabilityZone: Placement.AvailabilityZone, "
    "LaunchTime: LaunchTime, ImageId: ImageId, KeyName: KeyName}"
)
_OBJECT_SUMMARY = "Contents[].{Key: Key, Size: Size, LastModified: LastModified, StorageClass: StorageClass}"


class AwsActionParams(BaseModel):
    operation: Literal[_OPERATIONS] = "whoami"

    # Shared: blank means the default region saved with the credential.
    region: str = Field(
        default="",
        description="AWS region for this call (blank = the default region saved in Credentials -> AWS)",
        json_schema_extra={"placeholder": "us-east-1", **_REGIONAL},
    )

    # ec2
    instance_id: str = Field(
        default="",
        description="EC2 instance id",
        json_schema_extra={"placeholder": "i-0123456789abcdef0", **_INSTANCE},
    )
    instance_state: Literal["all", "pending", "running", "stopping", "stopped", "shutting-down", "terminated"] = Field(
        default="all",
        description="Only list instances in this state",
        json_schema_extra=_INSTANCES_LIST,
    )

    # s3
    bucket: str = Field(
        default="",
        description="S3 bucket name (s3_list without a bucket lists the buckets)",
        json_schema_extra={"placeholder": "my-bucket", **_BUCKET},
    )
    prefix: str = Field(
        default="",
        description="Only list object keys starting with this prefix",
        json_schema_extra={"placeholder": "reports/2026/", **_PREFIX},
    )
    key: str = Field(
        default="",
        description="S3 object key (s3_upload defaults to the file's name)",
        json_schema_extra={"placeholder": "reports/q3.pdf", **_KEY},
    )
    # Three shapes reach this field: a file reference from an upstream node or
    # the upload route, a bare workspace path, or the legacy base64 envelope.
    # coerce_file_param reads all three with workspace containment.
    file: Any = Field(
        default="",
        description="File to upload: upload one, or point at a workspace path",
        json_schema_extra={"widget": "file", **_UPLOAD},
    )

    # call / list_operations
    service: str = Field(
        default="",
        description="boto3 service name (ec2, s3, lambda, iam, rds, cloudwatch, ...)",
        json_schema_extra={
            "placeholder": "ec2",
            "dynamicOptions": True,
            "loadOptionsMethod": "awsServices",
            **_SERVICE,
        },
    )
    api_operation: str = Field(
        default="",
        description="boto3 method of the service, e.g. describe_vpcs (DescribeVpcs also works)",
        json_schema_extra={
            "placeholder": "describe_vpcs",
            "dynamicOptions": True,
            "loadOptionsMethod": "awsOperations",
            "loadOptionsDependsOn": ["service"],
            **_CALL,
        },
    )
    request: Dict[str, Any] = Field(
        default_factory=dict,
        description="API parameters as a JSON object, with AWS's own PascalCase names",
        json_schema_extra={"editor": "json", "rows": 6, **_CALL},
    )
    search: str = Field(
        default="",
        description="Case-insensitive substring over service or operation names",
        json_schema_extra={"placeholder": "vpc", **_SEARCH},
    )

    limit: int = Field(default=100, ge=1, le=1000, json_schema_extra=_LIMIT)

    model_config = ConfigDict(extra="ignore")

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, values: Any) -> Any:
        # The panel stores "" for cleared fields and renders `request` as
        # text; an LLM may also stringify the JSON object.
        return coerce_blank_params(cls, values, object_fields=("request",))


class AwsActionOutput(BaseModel):
    operation: Optional[str] = None
    region: Optional[str] = None
    service: Optional[str] = None
    api_operation: Optional[str] = None
    result: Optional[Any] = None
    count: Optional[int] = None
    total: Optional[int] = None
    truncated: Optional[bool] = None
    file: Optional[Dict[str, Any]] = None

    model_config = ConfigDict(extra="allow")


class AwsActionNode(ActionNode):
    type = "awsAction"
    display_name = "AWS"
    subtitle = "Amazon Web Services"
    group = ("deployment", "tool")
    description = (
        "Amazon Web Services through the official boto3 SDK: EC2 instances, S3 storage, "
        "or any AWS API operation"
    )
    component_kind = "square"
    tool_name = "aws_action"
    tool_description = (
        "Work with Amazon Web Services through the official boto3 SDK, authenticated by the access key "
        "saved in Credentials -> AWS. Operations: whoami (the account and IAM identity behind the key; run "
        "it first when unsure); ec2_instances_list (one row per instance: id, name, state, type, IPs; "
        "optional instance_state filter), ec2_instance_describe / ec2_instance_start / ec2_instance_stop "
        "(instance_id required); s3_list (the buckets, or a bucket's objects under prefix), s3_upload "
        "(bucket, file = a workspace path or file reference, key defaults to the file name), s3_download "
        "(bucket + key; saves the object to the workspace and returns a file reference), s3_delete "
        "(bucket + key); call (any other AWS API operation: service is the boto3 service name such as "
        "ec2, s3, lambda, iam, rds; api_operation the boto3 method such as describe_vpcs; request the API "
        "parameters as a JSON object with AWS's own PascalCase names, e.g. {\"Filters\": [{\"Name\": "
        "\"tag:Name\", \"Values\": [\"web\"]}]}); list_operations (no service: every service name; with a "
        "service: its operations and parameter names; needs no credentials). region overrides the "
        "credential's default region for one call. Results are AWS's own response fields, timestamps in "
        "ISO 8601. Reference: https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/index.html"
    )
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},
    )
    # start / stop / s3_delete / call mutate cloud state.
    annotations = {"destructive": True, "readonly": False, "open_world": True}
    credentials = (AwsCredential,)
    task_queue = TaskQueue.REST_API
    usable_as_tool = True
    # usable_as_tool auto-sets both hide flags unless declared. This node
    # stays wirable on the canvas through its declared `handles`; the
    # frontend reads the flags only for a spec with no handles.
    hide_input_handle = False
    hide_output_handle = False

    Params = AwsActionParams
    Output = AwsActionOutput

    # ---- shared plumbing -------------------------------------------------

    @staticmethod
    def _shape(operation: str, **fields: Any) -> Dict[str, Any]:
        """``{"operation": ...}`` plus every field that is not ``None``. An empty
        ``[]`` / ``{}`` result is a real answer from AWS and is kept."""
        shaped: Dict[str, Any] = {"operation": operation}
        shaped.update({k: v for k, v in fields.items() if v is not None})
        return shaped

    @staticmethod
    def _required(value: str, field: str, operation: str, example: str) -> str:
        value = value.strip()
        if not value:
            raise NodeUserError(f"{field} is required for {operation} (e.g. {example})")
        return value

    async def _keys(self, ctx: NodeContext, params: AwsActionParams) -> Tuple[_sdk.KeyPair, str]:
        """The credential is resolved first, so a missing key surfaces as the
        ``PermissionDeniedError`` envelope that points at the modal."""
        secrets = await AwsCredential.resolve(user_id=ctx.credential_customer_id)
        region = params.region.strip() or (secrets.get(REGION_FIELD) or "").strip() or DEFAULT_REGION
        return _sdk.key_pair(secrets), region

    # ---- identity ----------------------------------------------------------

    @Operation("whoami")
    async def whoami(self, ctx: NodeContext, params: AwsActionParams) -> Any:
        keys, region = await self._keys(ctx, params)
        identity = await _sdk.call(keys, "sts", "get_caller_identity", {}, region)
        return self._shape("whoami", region=region, result=identity)

    # ---- ec2 ---------------------------------------------------------------

    @Operation("ec2_instances_list")
    async def ec2_instances_list(self, ctx: NodeContext, params: AwsActionParams) -> Any:
        keys, region = await self._keys(ctx, params)
        request: Dict[str, Any] = {}
        if params.instance_state != "all":
            request["Filters"] = [{"Name": "instance-state-name", "Values": [params.instance_state]}]
        items, truncated = await _sdk.collect(
            keys, "ec2", "describe_instances", request, region, _INSTANCE_SUMMARY, params.limit
        )
        return self._shape("ec2_instances_list", region=region, result=items, count=len(items), truncated=truncated)

    @Operation("ec2_instance_describe")
    async def ec2_instance_describe(self, ctx: NodeContext, params: AwsActionParams) -> Any:
        instance_id = self._required(params.instance_id, "instance_id", "ec2_instance_describe", "i-0123456789abcdef0")
        keys, region = await self._keys(ctx, params)
        items, _ = await _sdk.collect(
            keys, "ec2", "describe_instances", {"InstanceIds": [instance_id]}, region, "Reservations[].Instances[]", 1
        )
        if not items:
            raise NodeUserError(f"No EC2 instance {instance_id} in {region}")
        return self._shape("ec2_instance_describe", region=region, result=items[0])

    @Operation("ec2_instance_start")
    async def ec2_instance_start(self, ctx: NodeContext, params: AwsActionParams) -> Any:
        instance_id = self._required(params.instance_id, "instance_id", "ec2_instance_start", "i-0123456789abcdef0")
        keys, region = await self._keys(ctx, params)
        response = await _sdk.call(keys, "ec2", "start_instances", {"InstanceIds": [instance_id]}, region)
        return self._shape("ec2_instance_start", region=region, result=response)

    @Operation("ec2_instance_stop")
    async def ec2_instance_stop(self, ctx: NodeContext, params: AwsActionParams) -> Any:
        instance_id = self._required(params.instance_id, "instance_id", "ec2_instance_stop", "i-0123456789abcdef0")
        keys, region = await self._keys(ctx, params)
        response = await _sdk.call(keys, "ec2", "stop_instances", {"InstanceIds": [instance_id]}, region)
        return self._shape("ec2_instance_stop", region=region, result=response)

    # ---- s3 ----------------------------------------------------------------

    @Operation("s3_list")
    async def s3_list(self, ctx: NodeContext, params: AwsActionParams) -> Any:
        keys, region = await self._keys(ctx, params)
        bucket = params.bucket.strip()
        if not bucket:
            buckets = (await _sdk.call(keys, "s3", "list_buckets", {}, region)).get("Buckets", [])
            shown = buckets[: params.limit]
            return self._shape(
                "s3_list", region=region, result=shown, count=len(shown), truncated=len(buckets) > params.limit
            )
        items, truncated = await _sdk.collect(
            keys, "s3", "list_objects_v2", {"Bucket": bucket, "Prefix": params.prefix}, region,
            _OBJECT_SUMMARY, params.limit,
        )
        return self._shape("s3_list", region=region, result=items, count=len(items), truncated=truncated)

    @Operation("s3_upload")
    async def s3_upload(self, ctx: NodeContext, params: AwsActionParams) -> Any:
        bucket = self._required(params.bucket, "bucket", "s3_upload", "my-bucket")
        if not params.file:
            raise NodeUserError(
                "file is required for s3_upload: a workspace path such as 'reports/q3.pdf', "
                "or a file from an upstream node"
            )
        filename, payload = coerce_file_param(params.file, ctx=ctx)
        key = params.key.strip() or filename
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        keys, region = await self._keys(ctx, params)
        response = await _sdk.call(
            keys, "s3", "put_object",
            {"Bucket": bucket, "Key": key, "Body": payload, "ContentType": content_type}, region,
        )
        return self._shape(
            "s3_upload", region=region, result={"Bucket": bucket, "Key": key, "Size": len(payload), **response}
        )

    @Operation("s3_download")
    async def s3_download(self, ctx: NodeContext, params: AwsActionParams) -> Any:
        bucket = self._required(params.bucket, "bucket", "s3_download", "my-bucket")
        key = self._required(params.key, "key", "s3_download", "reports/q3.pdf")
        keys, region = await self._keys(ctx, params)
        payload, content_type = await _sdk.download_object(keys, bucket, key, region, MEDIA_MAX_READ_BYTES)
        name = PurePosixPath(key).name
        ref = write_media(
            payload,
            ctx=ctx,
            stem=PurePosixPath(name).stem or "object",
            ext=PurePosixPath(name).suffix or ".bin",
            mime_type=content_type,
        )
        return self._shape(
            "s3_download",
            region=region,
            result={"Bucket": bucket, "Key": key, "Size": len(payload), "ContentType": content_type},
            file=ref.model_dump(mode="json"),
        )

    @Operation("s3_delete")
    async def s3_delete(self, ctx: NodeContext, params: AwsActionParams) -> Any:
        bucket = self._required(params.bucket, "bucket", "s3_delete", "my-bucket")
        key = self._required(params.key, "key", "s3_delete", "reports/q3.pdf")
        keys, region = await self._keys(ctx, params)
        response = await _sdk.call(keys, "s3", "delete_object", {"Bucket": bucket, "Key": key}, region)
        return self._shape("s3_delete", region=region, result={"Bucket": bucket, "Key": key, **response})

    # ---- any operation -----------------------------------------------------

    @Operation("call")
    async def call(self, ctx: NodeContext, params: AwsActionParams) -> Any:
        service = self._required(params.service, "service", "call", "ec2")
        api_operation = self._required(params.api_operation, "api_operation", "call", "describe_vpcs")
        # Resolved before any credential or network use, with the closest
        # names on a miss.
        info = await asyncio.to_thread(_sdk.resolve_operation, service, api_operation)
        keys, region = await self._keys(ctx, params)
        response = await _sdk.call(keys, info.service, info.operation, params.request, region)
        return self._shape(
            "call", region=region, service=info.service, api_operation=info.operation, result=response
        )

    @Operation("list_operations")
    async def list_operations(self, ctx: NodeContext, params: AwsActionParams) -> Any:
        # Pure introspection of the installed SDK: no credentials, no network.
        needle = params.search.strip().lower()
        service = params.service.strip()
        if not service:
            names = [name for name in await asyncio.to_thread(_sdk.service_names) if needle in name]
            shown = names[: params.limit]
            return self._shape("list_operations", result=shown, count=len(shown), total=len(names))
        infos = await asyncio.to_thread(_sdk.operations, service)
        matches = [
            info for info in infos if not needle or needle in info.operation or needle in info.summary.lower()
        ]
        shown = matches[: params.limit]
        return self._shape(
            "list_operations",
            service=infos[0].service if infos else service,
            result=[info.as_dict() for info in shown],
            count=len(shown),
            total=len(matches),
        )


__all__ = ["AwsActionNode", "AwsActionOutput", "AwsActionParams"]
