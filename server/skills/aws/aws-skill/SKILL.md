---
name: aws-skill
description: Work with Amazon Web Services through the official boto3 SDK — check the identity behind the key, list, start and stop EC2 instances, list, upload, download and delete S3 objects, and call any other AWS API operation. Output is AWS's own JSON response.
allowed-tools: "aws_action"
metadata:
  author: opencompany
  version: "1.0"
  category: deployment

---

# AWS Skill

Wrapper over the official [AWS SDK for Python (boto3)](https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/index.html).
Typed operations for the common EC2 and S3 flows, plus `call`, which
reaches every operation of every AWS service, and `list_operations` to
discover them. Results are AWS's own response fields, with timestamps in
ISO 8601 and binary values in base64, as the AWS CLI prints them.

## Tool: aws_action

### Operations

| Operation | Purpose | Key fields |
|---|---|---|
| `whoami` | The account and IAM identity behind the saved key | `region` (optional) |
| `ec2_instances_list` | One row per instance: id, name, state, type, IPs, zone, launch time | `instance_state` (default `all`), `limit`, `region` |
| `ec2_instance_describe` | Every field of one instance | `instance_id` (required), `region` |
| `ec2_instance_start` | Start an instance | `instance_id` (required), `region` |
| `ec2_instance_stop` | Stop an instance | `instance_id` (required), `region` |
| `s3_list` | The buckets, or a bucket's objects under a prefix | `bucket` (omit to list buckets), `prefix`, `limit` |
| `s3_upload` | Upload a workspace file | `bucket`, `file` (required), `key` (defaults to the file name) |
| `s3_download` | Save an object into the workspace and get a file reference | `bucket`, `key` (required) |
| `s3_delete` | Delete one object | `bucket`, `key` (required) |
| `call` | Any other AWS API operation | `service`, `api_operation` (required), `request`, `region` |
| `list_operations` | Discover services, or one service's operations and parameters | `service` (optional), `search`, `limit` |

`region` overrides the default region saved with the credential for one
call. EC2 instances are regional: list them in the region you mean.

### Response

```json
{
  "operation": "ec2_instances_list",
  "region": "us-east-1",
  "result": [
    { "InstanceId": "i-0abc123", "Name": "web-1", "State": "running", "InstanceType": "t3.micro", "PublicIpAddress": "203.0.113.10" }
  ],
  "count": 1,
  "truncated": false
}
```

`truncated: true` means there were more items than `limit`. On failure the
tool raises an error carrying AWS's own error code and message: surface it
verbatim, because it names the exact permission, parameter or resource.

## Typical flows

Orient first when the account or region is unknown:

```json
{ "operation": "whoami" }
{ "operation": "ec2_instances_list", "instance_state": "running" }
{ "operation": "ec2_instance_stop", "instance_id": "i-0abc123" }
```

S3. Local files are workspace paths; a download returns a file reference
under `file` that other nodes and tools can use:

```json
{ "operation": "s3_list" }
{ "operation": "s3_list", "bucket": "my-bucket", "prefix": "reports/" }
{ "operation": "s3_upload", "bucket": "my-bucket", "file": "reports/q3.pdf", "key": "reports/2026/q3.pdf" }
{ "operation": "s3_download", "bucket": "my-bucket", "key": "reports/2026/q3.pdf" }
```

Uploads and downloads are limited to 25 MB per file.

## The full AWS surface via call

Discover, then call. `list_operations` needs no credentials and makes no
request:

```json
{ "operation": "list_operations", "search": "lambda" }
{ "operation": "list_operations", "service": "lambda", "search": "function" }
```

Each operation lists its `required` and accepted `params`. Then call it
with the boto3 method name and the API parameters in AWS's own PascalCase:

```json
{ "operation": "call", "service": "ec2", "api_operation": "describe_vpcs" }
{ "operation": "call", "service": "ec2", "api_operation": "describe_instances", "request": { "Filters": [{ "Name": "tag:Name", "Values": ["web-*"] }] } }
{ "operation": "call", "service": "lambda", "api_operation": "list_functions" }
{ "operation": "call", "service": "lambda", "api_operation": "invoke", "request": { "FunctionName": "my-fn", "Payload": "{\"ping\": true}" } }
{ "operation": "call", "service": "iam", "api_operation": "list_roles", "request": { "MaxItems": 20 } }
{ "operation": "call", "service": "cloudwatch", "api_operation": "describe_alarms", "request": { "StateValue": "ALARM" } }
```

Notes for `call`:

- `api_operation` accepts `describe_vpcs` or `DescribeVpcs`; an unknown
  name fails before any request, with the closest names.
- `request` is checked against the API model before sending; a wrong
  parameter name fails with AWS's own validation message.
- One page per call. A truncated answer carries `NextToken` (or the
  service's own marker); pass it back in `request` for the next page.
- A streaming response (Lambda `Payload`) comes back inline as text up
  to 1 MB; use `s3_download` for S3 objects.

## Authentication

The tool uses the IAM access key saved in **Credentials -> AWS** (Access
Key ID, Secret Access Key, optional default region). AWS credentials
configured elsewhere on the server are never used.

If a call fails with `InvalidClientTokenId` or `SignatureDoesNotMatch`,
the saved key is wrong or deleted: point the user at Credentials -> AWS.
If it fails with `AccessDenied` / `UnauthorizedOperation`, the IAM
identity lacks that permission: name the action from the message. Never
ask the user to paste keys in chat.

## Best practices

1. **Run `whoami` first** when unsure which account or region you are in.
2. **Confirm destructive operations** (instance stop, `s3_delete`,
   terminate / delete calls via `call`) with the user unless they asked
   explicitly.
3. **Prefer filters over long listings**: `instance_state`, `prefix`, or
   `Filters` in `request`.
4. **Surface AWS error messages verbatim**; they name the exact fix.
5. **Costs are real**: starting instances and creating resources bill the
   user's AWS account; mention it when the intent is exploratory.
