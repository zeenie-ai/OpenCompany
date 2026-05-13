"""Google Contacts — Wave 11.D.4 inlined (People API v1)."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.plugin import ActionNode, NodeContext, Operation, TaskQueue

from .._credentials import GoogleCredential

from .._base import build_google_service, run_sync, track_google_usage


_CREATE = {"displayOptions": {"show": {"operation": ["create"]}}}
_LIST = {"displayOptions": {"show": {"operation": ["list"]}}}
_SEARCH = {"displayOptions": {"show": {"operation": ["search"]}}}
_LIST_OR_SEARCH = {"displayOptions": {"show": {"operation": ["list", "search"]}}}
_GET_UPDATE_DELETE = {"displayOptions": {"show": {"operation": ["get", "update", "delete"]}}}
_UPDATE = {"displayOptions": {"show": {"operation": ["update"]}}}


class ContactsParams(BaseModel):
    operation: Literal["create", "list", "search", "get", "update", "delete"] = "list"
    resource_name: Optional[str] = Field(default=None, json_schema_extra=_GET_UPDATE_DELETE)

    # Create fields
    first_name: Optional[str] = Field(default=None, json_schema_extra=_CREATE)
    last_name: Optional[str] = Field(default=None, json_schema_extra=_CREATE)
    email: Optional[str] = Field(default=None, json_schema_extra=_CREATE)
    phone: Optional[str] = Field(default=None, json_schema_extra=_CREATE)
    company: Optional[str] = Field(default=None, json_schema_extra=_CREATE)
    job_title: Optional[str] = Field(default=None, json_schema_extra=_CREATE)
    notes: Optional[str] = Field(default=None, json_schema_extra=_CREATE)

    # Search
    query: Optional[str] = Field(default=None, json_schema_extra=_SEARCH)

    # List + search pagination
    page_size: int = Field(default=100, ge=1, le=1000, json_schema_extra=_LIST_OR_SEARCH)
    page_token: Optional[str] = Field(default=None, json_schema_extra=_LIST)
    sort_order: Literal[
        "LAST_MODIFIED_ASCENDING", "LAST_MODIFIED_DESCENDING",
        "FIRST_NAME_ASCENDING", "LAST_NAME_ASCENDING",
    ] = Field(default="LAST_MODIFIED_DESCENDING", json_schema_extra=_LIST)

    # Update-only
    update_first_name: Optional[str] = Field(default=None, json_schema_extra=_UPDATE)
    update_last_name: Optional[str] = Field(default=None, json_schema_extra=_UPDATE)
    update_email: Optional[str] = Field(default=None, json_schema_extra=_UPDATE)
    update_phone: Optional[str] = Field(default=None, json_schema_extra=_UPDATE)
    update_company: Optional[str] = Field(default=None, json_schema_extra=_UPDATE)
    update_job_title: Optional[str] = Field(default=None, json_schema_extra=_UPDATE)

    model_config = ConfigDict(extra="ignore")


class ContactsOutput(BaseModel):
    operation: Optional[str] = None
    contact: Optional[dict] = None
    contacts: Optional[List[dict]] = None
    count: Optional[int] = None
    total_people: Optional[int] = None
    next_page_token: Optional[str] = None
    deleted: Optional[bool] = None
    resource_name: Optional[str] = None

    model_config = ConfigDict(extra="allow")


def _format_contact(person: Dict[str, Any]) -> Dict[str, Any]:
    names = person.get('names', [{}])
    emails = person.get('emailAddresses', [])
    phones = person.get('phoneNumbers', [])
    orgs = person.get('organizations', [])
    photos = person.get('photos', [])

    primary_name = names[0] if names else {}
    primary_email = next((e for e in emails if e.get('metadata', {}).get('primary')), emails[0] if emails else {})
    primary_phone = next((p for p in phones if p.get('metadata', {}).get('primary')), phones[0] if phones else {})
    primary_org = orgs[0] if orgs else {}
    primary_photo = next((p for p in photos if p.get('metadata', {}).get('primary')), photos[0] if photos else {})

    return {
        'resource_name': person.get('resourceName'),
        'display_name': primary_name.get('displayName', ''),
        'given_name': primary_name.get('givenName', ''),
        'family_name': primary_name.get('familyName', ''),
        'email': primary_email.get('value', ''),
        'phone': primary_phone.get('value', ''),
        'company': primary_org.get('name', ''),
        'job_title': primary_org.get('title', ''),
        'photo_url': primary_photo.get('url', ''),
        'emails': [e.get('value') for e in emails],
        'phones': [p.get('value') for p in phones],
    }


class ContactsNode(ActionNode):
    type = "googleContacts"
    display_name = "Contacts"
    subtitle = "Contact Management"
    group = ("google", "tool")
    description = "Google Contacts create / list / search / get / update / delete"
    component_kind = "square"
    handles = (
        {"name": "input-main", "kind": "input", "position": "left",
         "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right",
         "label": "Output", "role": "main"},
    )
    annotations = {"destructive": False, "readonly": False, "open_world": True}
    credentials = (GoogleCredential,)
    task_queue = TaskQueue.REST_API
    usable_as_tool = True

    Params = ContactsParams
    Output = ContactsOutput

    @Operation("dispatch", cost={"service": "contacts", "action": "op", "count": 1})
    async def dispatch(self, ctx: NodeContext, params: ContactsParams) -> ContactsOutput:
        svc = await build_google_service("people", "v1", params.model_dump(), ctx.raw)
        people_svc = svc.people()
        op = params.operation

        if op == "create":
            if not params.first_name:
                raise RuntimeError("First name is required")
            body: Dict[str, Any] = {
                'names': [{'givenName': params.first_name, 'familyName': params.last_name or ''}],
            }
            if params.email:
                body['emailAddresses'] = [{'value': params.email}]
            if params.phone:
                body['phoneNumbers'] = [{'value': params.phone}]
            if params.company or params.job_title:
                body['organizations'] = [{'name': params.company or '', 'title': params.job_title or ''}]
            if params.notes:
                body['biographies'] = [{'value': params.notes, 'contentType': 'TEXT_PLAIN'}]

            result = await run_sync(lambda: people_svc.createContact(body=body).execute())
            await track_google_usage("google_contacts", ctx.node_id, "create", 1, ctx.raw)
            return ContactsOutput(operation="create", contact=_format_contact(result))

        if op == "list":
            list_kwargs = {
                'resourceName': 'people/me',
                'pageSize': params.page_size,
                'personFields': 'names,emailAddresses,phoneNumbers,organizations,photos,biographies',
                'sortOrder': params.sort_order,
            }
            if params.page_token:
                list_kwargs['pageToken'] = params.page_token
            result = await run_sync(lambda: people_svc.connections().list(**list_kwargs).execute())
            connections = result.get('connections', [])
            contacts = [_format_contact(p) for p in connections]
            await track_google_usage("google_contacts", ctx.node_id, "list", len(contacts), ctx.raw)
            return ContactsOutput(
                operation="list",
                contacts=contacts,
                count=len(contacts),
                total_people=result.get('totalPeople', 0),
                next_page_token=result.get('nextPageToken'),
            )

        if op == "search":
            if not params.query:
                raise RuntimeError("Search query is required")
            result = await run_sync(lambda: people_svc.searchContacts(
                query=params.query,
                pageSize=min(params.page_size, 30),
                readMask='names,emailAddresses,phoneNumbers,organizations,photos',
            ).execute())
            contacts = [_format_contact(r.get('person', {})) for r in result.get('results', [])]
            await track_google_usage("google_contacts", ctx.node_id, "search", len(contacts), ctx.raw)
            return ContactsOutput(operation="search", contacts=contacts, count=len(contacts))

        if op == "get":
            if not params.resource_name:
                raise RuntimeError("Resource name is required")
            result = await run_sync(lambda: people_svc.get(
                resourceName=params.resource_name,
                personFields='names,emailAddresses,phoneNumbers,organizations,photos,biographies,addresses',
            ).execute())
            await track_google_usage("google_contacts", ctx.node_id, "get", 1, ctx.raw)
            return ContactsOutput(operation="get", contact=_format_contact(result))

        if op == "delete":
            if not params.resource_name:
                raise RuntimeError("Resource name is required")
            await run_sync(lambda: people_svc.deleteContact(resourceName=params.resource_name).execute())
            await track_google_usage("google_contacts", ctx.node_id, "delete", 1, ctx.raw)
            return ContactsOutput(operation="delete", deleted=True, resource_name=params.resource_name)

        if op == "update":
            if not params.resource_name:
                raise RuntimeError("Resource name is required")

            current = await run_sync(lambda: people_svc.get(
                resourceName=params.resource_name,
                personFields='names,emailAddresses,phoneNumbers,organizations,metadata',
            ).execute())

            first = params.update_first_name or params.first_name
            last = params.update_last_name or params.last_name
            email = params.update_email or params.email
            phone = params.update_phone or params.phone
            company = params.update_company or params.company
            job_title = params.update_job_title or params.job_title

            body: Dict[str, Any] = {'etag': current.get('etag')}
            update_fields: List[str] = []

            if first or last:
                cur_names = current.get('names', [{}])[0]
                body['names'] = [{
                    'givenName': first or cur_names.get('givenName', ''),
                    'familyName': last or cur_names.get('familyName', ''),
                }]
                update_fields.append('names')
            if email:
                body['emailAddresses'] = [{'value': email}]
                update_fields.append('emailAddresses')
            if phone:
                body['phoneNumbers'] = [{'value': phone}]
                update_fields.append('phoneNumbers')
            if company or job_title:
                body['organizations'] = [{'name': company or '', 'title': job_title or ''}]
                update_fields.append('organizations')

            if not update_fields:
                raise RuntimeError("At least one field to update is required")

            result = await run_sync(lambda: people_svc.updateContact(
                resourceName=params.resource_name,
                body=body,
                updatePersonFields=','.join(update_fields),
            ).execute())
            await track_google_usage("google_contacts", ctx.node_id, "update", 1, ctx.raw)
            return ContactsOutput(operation="update", contact=_format_contact(result))

        raise RuntimeError(f"Unknown Contacts operation: {op}")
