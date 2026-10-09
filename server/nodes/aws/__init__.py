"""Plugin for the 'deployment' palette group — Amazon Web Services via the
official boto3 SDK.

One dual-purpose node, ``awsAction`` (AI tool ``aws_action``): typed EC2 / S3
operations, ``call`` for any AWS API operation, and ``list_operations`` to
discover them. Auth is an IAM access key pair saved in Credentials -> AWS and
passed to boto3 explicitly; the SDK is imported lazily (``_sdk.py``), so the
plugin registers and the credentials modal answers even without it.
"""

from __future__ import annotations

from services.node_output_schemas import register_output_schema
from services.ws_handler_registry import register_option_loader

from ._credentials import AwsCredential
from ._sdk import load_aws_operations, load_aws_services
from .aws_action import AwsActionNode, AwsActionOutput

register_output_schema("awsAction", AwsActionOutput)
register_option_loader("awsServices", load_aws_services)
register_option_loader("awsOperations", load_aws_operations)

__all__ = [
    "AwsActionNode",
    "AwsCredential",
]
