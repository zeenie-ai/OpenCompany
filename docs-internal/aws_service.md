# AWS Service

Self-contained plugin at [`server/nodes/aws/`](../server/nodes/aws/) over the
official **AWS SDK for Python** (`boto3`, a server dependency). One
dual-purpose node, `awsAction` (workflow node + AI tool `aws_action`): typed
operations for the common flows (identity, EC2 instances, S3 objects), `call`
for any operation of any AWS service, and `list_operations` to discover them.

**Why the SDK and not the AWS CLI** (unlike the gcloud / cf / gh plugins): AWS
CLI v2 ships as an MSI on Windows and a `.pkg` on macOS, with no portable
archive to pin and extract the way `gcloud/_install.py` does, and its browser
login (`aws login`) cannot run on a remote or hosted server. The SDK installs
with `uv sync` on every platform, and a stored key pair works headless.

**Auth model: an IAM access key pair in OpenCompany's encrypted store.**
`AwsCredential` (`ApiKeyCredential`, id `aws`) keeps three rows: the Secret
Access Key under the provider id (the field the credentials modal validates),
`aws_access_key_id`, and the optional `aws_region` (`extra_fields`, the
cloudflare companion-field pattern). The modal probe signs
`sts:GetCallerIdentity` with the pair; every IAM identity may call it, so a
least-privilege key validates too. The Access Key ID must be saved before the
secret is validated, and the probe says so when it is missing.

**No ambient credentials.** Every client is created with the key pair passed
explicitly, which outranks the SDK's credential chain, so environment
variables, `~/.aws` and an EC2 instance profile on the server never
authenticate a node.

## File map

| File | Role |
|---|---|
| `__init__.py` | `register_output_schema("awsAction", …)` + `register_option_loader("awsServices" / "awsOperations", …)`; the node auto-registers on import |
| `aws_action.py` | `AwsActionNode(ActionNode)`, `usable_as_tool=True`, `tool_name="aws_action"`, both canvas handles kept visible, eleven `@Operation`s; `_keys` resolves the credential before any work, so a missing key gives the `PermissionDeniedError` envelope; `_shape` drops only `None` |
| `_sdk.py` | Every boto3 / botocore touch, imported lazily: introspection (`service_names`, `operations`, `resolve_operation` with `difflib` suggestions), calls (`call`, `collect` over a paginator with a JMESPath projection, `download_object`), `to_plain`, `raise_user_error`, and the two dropdown loaders |
| `_credentials.py` | `AwsCredential`: `resolve()` also requires the Access Key ID; `_probe()` reads the companion rows and calls STS |
| `icon.svg` / `aws.svg` / `meta.json` | Node icon and co-located credential icon (lobehub's AWS mark on a dark tile, readable through `<img>` on light and dark themes); colour `#FF9900` |

Paired skill: [`server/skills/aws/aws-skill/SKILL.md`](../server/skills/aws/aws-skill/SKILL.md)
(bound in `visuals.json`: `"awsAction": {"skill": "aws-skill"}`). Palette
group: `deployment`. Catalogue entry: `kind: "apiKey"` with the three fields
in `credential_providers.json`.

## Clients

One botocore session (module-level, cached) holds the service models and
client classes for every call; it holds no credentials. Each operation creates
a client with the key pair and region passed explicitly, about 5 ms once the
service's model is loaded (the first client of a service pays ~0.3 s). Client
creation is serialised by a lock because botocore sessions are not
thread-safe; the API calls run in worker threads (`asyncio.to_thread`)
concurrently. Every client uses 10 s connect / 60 s read timeouts and the
SDK's `standard` retry mode (3 attempts, throttling and transient errors only).

## Operations

| Op | SDK call | Notes |
|---|---|---|
| `whoami` | `sts.get_caller_identity` | |
| `ec2_instances_list` | `ec2.describe_instances` paginator, projected per instance (`InstanceId`, `Name` tag, `State`, type, IPs, zone, launch time, image, key) | `instance_state` filter; stops at `limit`, reports `truncated` |
| `ec2_instance_describe` | `ec2.describe_instances(InstanceIds=[id])` | The full instance |
| `ec2_instance_start` / `_stop` | `ec2.start_instances` / `stop_instances` | |
| `s3_list` | `s3.list_buckets`, or the `list_objects_v2` paginator (`Key`, `Size`, `LastModified`, `StorageClass`) | `prefix`; stops at `limit` |
| `s3_upload` | `s3.put_object` | `file` read by `coerce_file_param` (workspace containment, 25 MB); `key` defaults to the file name; content type from the name |
| `s3_download` | `s3.get_object` | Refused past `MEDIA_MAX_READ_BYTES` before reading; saved with `write_media`, returned as a `FileRef` under `file` |
| `s3_delete` | `s3.delete_object` | |
| `call` | `<service>.<api_operation>(**request)` | Resolved before any credential use; botocore validates `request` against the model before sending; one page per call |
| `list_operations` | botocore's bundled models | No credentials, no network |

Responses are shaped the way the AWS CLI prints them: `ResponseMetadata`
dropped, timestamps as ISO 8601, blobs as base64, a streaming body (Lambda
`Payload`) read inline up to 1 MB. Errors become `NodeUserError` with AWS's
code, message and request id, plus a hint for key (`InvalidClientTokenId`,
`SignatureDoesNotMatch`, ...), permission (`AccessDenied`,
`UnauthorizedOperation`) and throttling codes.

## Tests

[`server/tests/nodes/test_aws.py`](../server/tests/nodes/test_aws.py) replaces
`_sdk._client` with real botocore clients wrapped in `botocore.stub.Stubber`,
so requests are validated against the real API models without network:
registration and flat tool schema, region precedence, missing-credential
envelopes, AWS error mapping, the EC2 projection and truncation, S3 upload
containment and download to a `FileRef`, `call` resolution before
credentials, parameter validation, inline streaming bodies, `list_operations`
without credentials, the credential probe, a clean-interpreter check that
importing the plugin never loads boto3, and the catalogue / skill / asset
wiring. `aws` is also in `_MIGRATED_PLUGINS` of
`tests/test_plugin_self_containment.py`, and the tool name is pinned in
`tests/fixtures/tool_names_snapshot.json`.
