"""Google Sheets — Wave 11.D.4 inlined.

Reads / writes / appends cells via the Sheets v4 API.
"""

from __future__ import annotations

import json
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.plugin import ActionNode, NodeContext, Operation, TaskQueue

from .._credentials import GoogleCredential

from .._base import build_google_service, run_sync, track_google_usage


class SheetsParams(BaseModel):
    operation: Literal["read", "write", "append"] = "read"
    spreadsheet_id: str = Field(default="", description="Spreadsheet ID from the Sheets URL.")
    range: str = Field(default="A1:Z1000", description="A1 notation range (e.g. Sheet1!A1:C10).")

    values: Any = Field(
        default_factory=list,
        description="2D array of cells to write.",
        json_schema_extra={
            "rows": 4,
            "displayOptions": {"show": {"operation": ["write", "append"]}},
        },
    )
    value_input_option: Literal["RAW", "USER_ENTERED"] = Field(
        default="USER_ENTERED",
        description="RAW writes literal strings; USER_ENTERED evaluates formulas.",
        json_schema_extra={"displayOptions": {"show": {"operation": ["write", "append"]}}},
    )
    insert_data_option: Literal["INSERT_ROWS", "OVERWRITE"] = Field(
        default="INSERT_ROWS",
        json_schema_extra={"displayOptions": {"show": {"operation": ["append"]}}},
    )

    value_render_option: Literal["FORMATTED_VALUE", "UNFORMATTED_VALUE", "FORMULA"] = Field(
        default="FORMATTED_VALUE",
        json_schema_extra={"displayOptions": {"show": {"operation": ["read"]}}},
    )
    major_dimension: Literal["ROWS", "COLUMNS"] = Field(
        default="ROWS",
        json_schema_extra={"displayOptions": {"show": {"operation": ["read"]}}},
    )

    model_config = ConfigDict(extra="ignore")


class SheetsOutput(BaseModel):
    operation: Optional[str] = None
    values: Optional[List[List[Any]]] = None
    range: Optional[str] = None
    rows: Optional[int] = None
    columns: Optional[int] = None
    major_dimension: Optional[str] = None
    updated_range: Optional[str] = None
    updated_rows: Optional[int] = None
    updated_columns: Optional[int] = None
    updated_cells: Optional[int] = None
    table_range: Optional[str] = None

    model_config = ConfigDict(extra="allow")


def _coerce_values(raw: Any) -> List[List[Any]]:
    if isinstance(raw, str):
        raw = json.loads(raw)
    if raw and not isinstance(raw[0], list):
        raw = [raw]
    return raw


class SheetsNode(ActionNode):
    type = "googleSheets"
    display_name = "Sheets"
    subtitle = "Spreadsheet Ops"
    group = ("google", "tool")
    description = "Google Sheets read / write / append spreadsheet data"
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

    Params = SheetsParams
    Output = SheetsOutput

    @Operation("dispatch", cost={"service": "sheets", "action": "op", "count": 1})
    async def dispatch(self, ctx: NodeContext, params: SheetsParams) -> SheetsOutput:
        if not params.spreadsheet_id:
            raise RuntimeError("Spreadsheet ID is required")
        if not params.range:
            raise RuntimeError("Range is required (e.g., 'Sheet1!A1:D10')")

        svc = await build_google_service("sheets", "v4", params.model_dump(), ctx.raw)
        values_svc = svc.spreadsheets().values()
        op = params.operation

        if op == "read":
            result = await run_sync(lambda: values_svc.get(
                spreadsheetId=params.spreadsheet_id,
                range=params.range,
                valueRenderOption=params.value_render_option,
                majorDimension=params.major_dimension,
            ).execute())
            rows = result.get('values', [])
            await track_google_usage("google_sheets", ctx.node_id, "read", len(rows), ctx.raw)
            return SheetsOutput(
                operation="read",
                values=rows,
                range=result.get('range'),
                rows=len(rows),
                columns=len(rows[0]) if rows else 0,
                major_dimension=result.get('majorDimension'),
            )

        if op in ("write", "append"):
            values = _coerce_values(params.values)
            if not values:
                raise RuntimeError("Values are required")

            if op == "write":
                result = await run_sync(lambda: values_svc.update(
                    spreadsheetId=params.spreadsheet_id,
                    range=params.range,
                    valueInputOption=params.value_input_option,
                    body={'values': values},
                ).execute())
                await track_google_usage(
                    "google_sheets", ctx.node_id, "write",
                    result.get('updatedCells', 0), ctx.raw,
                )
                return SheetsOutput(
                    operation="write",
                    updated_range=result.get('updatedRange'),
                    updated_rows=result.get('updatedRows'),
                    updated_columns=result.get('updatedColumns'),
                    updated_cells=result.get('updatedCells'),
                )

            result = await run_sync(lambda: values_svc.append(
                spreadsheetId=params.spreadsheet_id,
                range=params.range,
                valueInputOption=params.value_input_option,
                insertDataOption=params.insert_data_option,
                body={'values': values},
            ).execute())
            updates = result.get('updates', {})
            await track_google_usage(
                "google_sheets", ctx.node_id, "append",
                updates.get('updatedCells', 0), ctx.raw,
            )
            return SheetsOutput(
                operation="append",
                updated_range=updates.get('updatedRange'),
                updated_rows=updates.get('updatedRows'),
                updated_columns=updates.get('updatedColumns'),
                updated_cells=updates.get('updatedCells'),
                table_range=result.get('tableRange'),
            )

        raise RuntimeError(f"Unknown Sheets operation: {op}. Supported: read, write, append")
