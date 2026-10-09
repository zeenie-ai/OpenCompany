"""AWS credential — an IAM access key pair kept in OpenCompany's encrypted
credential store (``ApiKeyCredential`` shape, cloudflare-style companion rows).

Three rows under one provider:

* ``apiKey`` (stored under the provider id ``aws``) — the Secret Access Key,
  the field the credentials modal validates;
* ``aws_access_key_id`` — the Access Key ID the secret belongs to;
* ``aws_region`` — the default region for calls that name none.

Validation signs ``sts:GetCallerIdentity`` with the pair: every IAM identity
may call it, so a least-privilege key validates too. The pair is always handed
to boto3 explicitly, so ambient AWS credentials on the server (environment
variables, ``~/.aws``, an instance profile) never authenticate a node.
"""

from __future__ import annotations

from typing import Any, Dict

from services.plugin.credential import ApiKeyCredential, ProbeResult

ACCESS_KEY_ID_FIELD = "aws_access_key_id"
REGION_FIELD = "aws_region"
DEFAULT_REGION = "us-east-1"


class AwsCredential(ApiKeyCredential):
    id = "aws"
    display_name = "AWS"
    category = "Deployment"
    docs_url = "https://docs.aws.amazon.com/IAM/latest/UserGuide/id_credentials_access-keys.html"
    extra_fields = (ACCESS_KEY_ID_FIELD, REGION_FIELD)

    @classmethod
    async def resolve(cls, *, user_id: str = "owner") -> Dict[str, Any]:
        """The secret, plus the Access Key ID it belongs to (required) and the
        default region (optional). A missing ID raises the same annotated
        ``PermissionError`` as a missing secret, so the node answers with the
        ``PermissionDeniedError`` envelope that points at the modal."""
        secrets = await super().resolve(user_id=user_id)
        if not secrets.get(ACCESS_KEY_ID_FIELD):
            err = PermissionError("No AWS Access Key ID saved. Add it in Credentials -> AWS.")
            err.provider = cls.id
            err.reason = "missing"
            err.auth = cls.auth
            raise err
        return secrets

    @classmethod
    async def _probe(cls, api_key: str) -> ProbeResult:
        """Sign ``sts:GetCallerIdentity`` with the stored Access Key ID and the
        secret being validated. Reads the companion rows (not a side effect);
        the base ``Credential.validate`` stores and broadcasts the result."""
        from services.plugin import NodeUserError
        from services.plugin.deps import get_auth_service

        from ._sdk import call, key_pair

        auth = get_auth_service()
        access_key_id = (await auth.get_api_key(ACCESS_KEY_ID_FIELD) or "").strip()
        if not access_key_id:
            return ProbeResult(
                valid=False,
                message="Fill the Access Key ID field below, Save Credentials, then validate again.",
            )
        region = (await auth.get_api_key(REGION_FIELD) or "").strip() or DEFAULT_REGION
        keys = key_pair({"api_key": api_key, ACCESS_KEY_ID_FIELD: access_key_id})
        try:
            identity = await call(keys, "sts", "get_caller_identity", {}, region)
        except NodeUserError as exc:
            return ProbeResult(valid=False, message=str(exc))
        arn = identity.get("Arn", "")
        return ProbeResult(
            valid=True,
            message=f"AWS access key is valid for {arn}",
            extra={"arn": arn, "account": identity.get("Account", "")},
        )
