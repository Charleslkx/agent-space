"""Typed spreadsheet layout, range, search and conditional-format MCP tools.

Registration receives the server's CLI runner to share authentication, error handling
and payload limits without coupling this module to server startup.
"""
from __future__ import annotations

import inspect
import re
from typing import Annotated, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

Spreadsheet = Annotated[str, Field(min_length=1, description="Spreadsheet URL or token; not a Docx or Base resource.")]
SheetID = Annotated[str | None, Field(description="Sub-sheet ID from sheets_info; supply exactly one of sheet_id or sheet_name.")]
SheetName = Annotated[str | None, Field(description="Exact sub-sheet name; supply exactly one of sheet_id or sheet_name.")]
A1Range = Annotated[str, Field(min_length=1, description="A1 range without a sheet prefix, e.g. A2:D20, A:A or 3:6; bounded ranges are preferred.")]
DimensionRange = Annotated[str, Field(min_length=1, description="Closed row/column interval: 3:7 or 5 for rows; C:F or C for columns. Rows are 1-based.")]
Position = Annotated[str, Field(pattern=r"^(?:[1-9][0-9]*|[A-Z]+)$", description="1-based row number or uppercase column letters, e.g. 3 or C; operation places items BEFORE this position.")]
DryRun = Annotated[bool, Field(description="True returns the CLI request preview without applying the operation; False executes it.")]
Confirmed = Annotated[bool, Field(description="True means the user authorized this destructive operation on the specified target. Required for execution; dry_run may preview without it.")]
RuleID = Annotated[str, Field(min_length=1, description="Conditional-format rule ID returned by sheets_list_conditional_formats.")]
PixelSize = Annotated[int, Field(strict=True, gt=0, description="Positive integer size in pixels, not points or Excel character units.")]
MaxChars = Annotated[int, Field(ge=1, le=500000, description="Maximum inline output characters. If truncated, narrow the range and read again.")]
ThresholdType = Literal["minValue", "maxValue", "num", "percentile", "percent", "formula", "auto"]
Comparison = Literal["greaterThan", "greaterThanOrEqual", "lessThan", "lessThanOrEqual"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SortKey(StrictModel):
    column: str = Field(pattern=r"^[A-Z]+$", description="Absolute spreadsheet column letters inside cell_range, e.g. C; not a zero-based index.")
    ascending: bool = Field(description="True sorts ascending; False descending. Array order defines sort priority.")


class ConditionalStyle(StrictModel):
    back_color: str | None = Field(default=None, description="Matched cell background color, e.g. #FF0000. Use for requests to highlight/color cells.")
    fore_color: str | None = Field(default=None, description="Matched cell font color, e.g. #000000; distinct from background color.")
    font: Literal["bold", "italic", "bold italic"] | None = Field(default=None, description="Matched cell font emphasis.")
    text_decoration: Literal["none", "underline", "strikethrough", "underline_strikethrough"] | None = Field(default=None, description="Matched cell text decoration.")


class CellComparison(StrictModel):
    compare_type: Literal["equal", "notEqual", "greaterThan", "greaterThanOrEqual", "lessThan", "lessThanOrEqual", "between", "notBetween"] = Field(description="Value comparison operator.")
    value: str = Field(description="Comparison value as text; between/notBetween use two comma-separated values, e.g. 10,100.")


class TextComparison(StrictModel):
    compare_type: Literal["beginsWith", "endsWith", "containsText", "notContains", "is"] = Field(description="Text matching operator.")
    text: str = Field(description="Text to match; is means exact equality.")


class TimePeriod(StrictModel):
    time_period: Literal["yesterday", "today", "tomorrow", "last7Days", "lastWeek", "thisWeek", "nextWeek", "lastMonth", "thisMonth", "nextMonth"] = Field(description="Relative date period interpreted by the spreadsheet service.")


class DataBarStop(StrictModel):
    color: str = Field(description="Data bar color, e.g. #638EC6.")
    value_type: ThresholdType = Field(description="Threshold interpretation: endpoint, fixed number, percentile, percent, formula or auto.")
    value: float | None = Field(default=None, description="Threshold value when required by value_type.")
    gradient: bool | None = Field(default=None, description="Whether the bar uses a gradient fill.")
    hide_value: bool | None = Field(default=None, description="Whether to hide the cell value and show only its bar.")


class ColorScaleStop(StrictModel):
    value_type: ThresholdType = Field(description="Threshold interpretation for this scale stop.")
    color: str = Field(description="Color at this scale stop, e.g. #FF0000.")
    value: float | None = Field(default=None, description="Threshold value when required by value_type.")


class RankAttribute(StrictModel):
    is_bottom: bool = Field(description="True selects bottom values; False selects top values.")
    value_type: Literal["percent", "sort"] = Field(description="percent selects a percentage; sort selects a count of ranked cells.")
    value: float | None = Field(default=None, description="Requested percentage or count.")


class AverageAttribute(StrictModel):
    operator: Comparison = Field(description="Comparison against the range average.")


class FormulaAttribute(StrictModel):
    formula: list[Annotated[str, Field(min_length=1)]] = Field(min_length=1, description='Formula strings, e.g. ["=AND($E2<>\"\",$E2<TODAY())"]. Use relative row references for row-wise rules and guard blank dates.')


class IconAttribute(StrictModel):
    icon_type: Literal["3Arrows", "3ArrowsGray", "3Triangles", "4ArrowsGray", "4Arrows", "5ArrowsGray", "5Arrows", "3Circles", "3MultiGraphics", "4Circles", "5Circles", "2Status", "3Status", "2CommentStatus", "3Flags", "3Stars", "3HeartShaped", "3Mood", "5CirclesRatio"] = Field(description="Icon set. 5CirclesRatio represents progress with empty/partial/full circles.")
    value_type: ThresholdType = Field(description="Interpretation of the icon threshold.")
    operator: Comparison = Field(description="Comparison against the icon threshold.")
    value: float | None = Field(default=None, description="Threshold value when required by value_type.")
    hide_value: bool | None = Field(default=None, description="Hide the cell value and show only the icon.")
    reverse_icons: bool | None = Field(default=None, description="Reverse icon order.")


class RuleBase(StrictModel):
    ranges: list[A1Range] = Field(min_length=1, description="All A1 ranges to which the rule applies; no sheet prefixes.")
    style: ConditionalStyle = Field(description="Matched cell style. Pass {} for rule types whose appearance is fully defined in attrs.")
    has_ref: bool | None = Field(default=None, description="Optional CLI flag marking whether the rule depends on referenced objects.")


class BasicRule(RuleBase):
    rule_type: Literal["duplicateValues", "uniqueValues", "containsBlanks", "notContainsBlanks"] = Field(description="Duplicate/unique/nonempty/empty rule; duplicateValues compares one column, not complete multi-column records.")


class CellRule(RuleBase):
    rule_type: Literal["cellIs"] = Field(description="Compare cell values against a threshold or interval.")
    attrs: list[CellComparison] = Field(min_length=1, description="Value comparison parameters.")


class TextRule(RuleBase):
    rule_type: Literal["containsText"] = Field(description="Apply a text matching condition.")
    attrs: list[TextComparison] = Field(min_length=1, description="Text matching parameters.")


class PeriodAttribute(TimePeriod):
    operator: Literal["before", "is", "after"] = Field(description="Match dates before, within, or after the selected relative period.")


class PeriodRule(RuleBase):
    rule_type: Literal["timePeriod"] = Field(description="Match a relative date period.")
    attrs: list[PeriodAttribute] = Field(min_length=1, description="Relative date parameters.")


class BarRule(RuleBase):
    rule_type: Literal["dataBar"] = Field(description="Render values as in-cell data bars.")
    attrs: list[DataBarStop] = Field(min_length=1, description="Data bar parameters and thresholds.")


class ScaleRule(RuleBase):
    rule_type: Literal["colorScale"] = Field(description="Render a two- or three-color scale.")
    attrs: list[ColorScaleStop] = Field(min_length=2, max_length=3, description="Minimum and maximum stops, or minimum/midpoint/maximum stops, in that order.")


class RankRule(RuleBase):
    rule_type: Literal["rank"] = Field(description="Highlight top/bottom values by count or percent.")
    attrs: list[RankAttribute] = Field(min_length=1, description="Rank selection parameters.")


class AverageRule(RuleBase):
    rule_type: Literal["aboveAverage"] = Field(description="Compare values with the range average.")
    attrs: list[AverageAttribute] = Field(min_length=1, description="Average comparison parameters.")


class ExpressionRule(RuleBase):
    rule_type: Literal["expression"] = Field(description="Use custom spreadsheet formulas as conditions.")
    attrs: list[FormulaAttribute] = Field(min_length=1, description="Custom formula parameters; exclude blank dates explicitly when checking overdue values.")


class IconRule(RuleBase):
    rule_type: Literal["iconSet"] = Field(description="Show threshold-dependent icon sets.")
    attrs: list[IconAttribute] = Field(min_length=1, description="Icon set and threshold parameters.")


ConditionalRule = Annotated[
    BasicRule | CellRule | TextRule | PeriodRule | BarRule | ScaleRule | RankRule | AverageRule | ExpressionRule | IconRule,
    Field(discriminator="rule_type", description="Complete rule definition. rule_type selects the required attrs schema; update replaces the whole rule."),
]


def _column_number(value: str) -> int:
    result = 0
    for char in value:
        result = result * 26 + ord(char) - ord("A") + 1
    return result


def _dimension_span(value: str) -> tuple[str, int, int]:
    if not re.fullmatch(r"(?:[1-9][0-9]*(?::[1-9][0-9]*)?|[A-Z]+(?::[A-Z]+)?)", value):
        raise ValueError("dimension range must be 1-based rows (3:7) or uppercase columns (C:F)")
    ends = value.split(":")
    axis = "rows" if ends[0].isdigit() else "columns"
    convert = int if axis == "rows" else _column_number
    start, end = convert(ends[0]), convert(ends[-1])
    if start > end:
        raise ValueError("dimension ranges must be in ascending order")
    return axis, start, end


def _nonoverlapping_dimensions(ranges: list[str]) -> list[tuple[str, int, int]]:
    spans = sorted(_dimension_span(value) for value in ranges)
    if len({span[0] for span in spans}) != 1:
        raise ValueError("row and column ranges cannot be mixed")
    if any(right[1] <= left[2] for left, right in zip(spans, spans[1:])):
        raise ValueError("dimension ranges must not overlap")
    return spans


def register_sheets_tools(mcp, meta: dict, call: Callable, target: Callable, encode: Callable) -> None:
    """Register tools against the host's shared CLI runner and sheet selector."""
    def tool(title: str, *, read: bool = False, destructive: bool = False, idempotent: bool = True):
        def register(fn):
            fn.__doc__ = inspect.cleandoc(fn.__doc__) + (
                "\n\nReturns:\n    CLI JSON envelope with ok, identity, data and optional meta. "
                "For tools exposing dry_run, True returns data describing the request preview, not an applied change.\n\n"
                "Raises:\n    Validation errors for invalid parameters or ambiguous sheet selectors; "
                "structured CLI errors for missing permissions, remote failures or timeouts. "
                "A failed write may have partially applied: read the affected cells/layout/rules "
                "before retrying; never blindly repeat structural edits."
            )
            return mcp.tool(title=title, description=fn.__doc__, meta=meta, annotations={
                "readOnlyHint": read, "destructiveHint": destructive,
                "idempotentHint": idempotent, "openWorldHint": True,
            })(fn)
        return register

    @tool("Read spreadsheet layout", read=True)
    def sheets_sheet_info(
        spreadsheet: Spreadsheet, sheet_id: SheetID = None, sheet_name: SheetName = None,
        include: Annotated[list[Literal["merges", "row_heights", "col_widths", "hidden_rows", "hidden_cols", "groups", "frozen"]] | None,
                           Field(description="Layout categories to return. Omit for CLI defaults; a supplied list must not be empty.")] = None,
        cell_range: Annotated[str | None, Field(description="Optional A1 range limiting layout inspection; omitted means the entire sub-sheet.")] = None,
    ) -> dict:
        """Read sub-sheet merges, sizes, hidden dimensions, outline groups and freeze state.

        Use sheets_info first for tab IDs; use this tool before structural edits.
        Layout inspection does not read values/formulas; use sheets_read for those.
        """
        if include == []:
            raise ValueError("include must not be empty")
        return call("sheet-info", {**target(spreadsheet, sheet_id, sheet_name),
                    "include": ",".join(include) if include is not None else None, "range": cell_range})

    @tool("Insert spreadsheet rows or columns", destructive=True, idempotent=False)
    def sheets_insert_dimensions(
        spreadsheet: Spreadsheet, position: Position,
        count: Annotated[int, Field(ge=1, description="Number of rows or columns to insert before position.")],
        sheet_id: SheetID = None, sheet_name: SheetName = None,
        inherit_style: Annotated[Literal["before", "after"], Field(description="Copy style from preceding or following dimension; does not change insertion direction. Row heights/column widths may require separate resizing.")] = "after",
        dry_run: DryRun = False,
    ) -> dict:
        """Insert complete rows or columns before a 1-based row number or column letter.

        Example: position="21", count=3 inserts after existing row 20. Read merges,
        nearby formulas and validation first. After insertion refetch shifted ranges
        and verify layout/style; repeating the call inserts additional dimensions.
        """
        _dimension_span(position)
        return call("dim-insert", {**target(spreadsheet, sheet_id, sheet_name),
                    "position": position, "count": count, "inherit-style": inherit_style}, write=True, dry_run=dry_run)

    @tool("Delete spreadsheet rows or columns", destructive=True, idempotent=False)
    def sheets_delete_dimensions(
        spreadsheet: Spreadsheet,
        ranges: Annotated[list[DimensionRange], Field(min_length=1, max_length=100, description="Nonoverlapping intervals in the same dimension. Coordinates all refer to the original pre-delete layout.")],
        sheet_id: SheetID = None, sheet_name: SheetName = None,
        confirmed: Confirmed = False, dry_run: DryRun = False,
    ) -> dict:
        """Delete complete rows or columns, including their content; this is irreversible.

        The CLI deletes intervals from highest to lowest position in one fail-fast
        batch, avoiding index shifts. Inspect target content and references first.
        Execute only with confirmed=True; dry_run previews without confirmation.
        A batch may partly apply, so inspect layout before retrying a failed call.
        """
        _nonoverlapping_dimensions(ranges)
        if not (confirmed or dry_run):
            raise ValueError("delete requires confirmed=True or dry_run=True")
        return call("dim-delete", {**target(spreadsheet, sheet_id, sheet_name), "ranges": encode(ranges)},
                    write=True, confirmed=confirmed, dry_run=dry_run)

    @tool("Move spreadsheet rows or columns", destructive=True, idempotent=False)
    def sheets_move_dimensions(
        spreadsheet: Spreadsheet, source_range: DimensionRange, position: Position,
        sheet_id: SheetID = None, sheet_name: SheetName = None, dry_run: DryRun = False,
    ) -> dict:
        """Move complete rows or columns before a position in the same dimension.

        source_range="3:7", position="12" moves those rows before row 12;
        source_range="C:F", position="H" moves columns. Both positions refer to
        the current layout. Read affected references first and refetch after moving.
        """
        axis, start, end = _dimension_span(source_range)
        target_axis, dest, _ = _dimension_span(position)
        if ":" in position or axis != target_axis or start <= dest <= end:
            raise ValueError("position must be one coordinate in the same dimension, outside source_range")
        return call("dim-move", {**target(spreadsheet, sheet_id, sheet_name),
                    "source-range": source_range, "target": position}, write=True, dry_run=dry_run)

    @tool("Set row or column visibility", destructive=True)
    def sheets_set_dimension_visibility(
        spreadsheet: Spreadsheet, dimension_range: DimensionRange,
        action: Annotated[Literal["hide", "unhide"], Field(description="Hide dimensions without deleting content, or show previously hidden dimensions.")],
        sheet_id: SheetID = None, sheet_name: SheetName = None, dry_run: DryRun = False,
    ) -> dict:
        """Hide or unhide a closed row/column interval without deleting data.

        Only change visibility when requested. Verify hidden_rows/hidden_cols with
        sheets_sheet_info; this tool does not change tab visibility.
        """
        _dimension_span(dimension_range)
        return call("dim-" + action, {**target(spreadsheet, sheet_id, sheet_name),
                    "range": dimension_range}, write=True, dry_run=dry_run)

    @tool("Manage row or column outline groups", destructive=True, idempotent=False)
    def sheets_group_dimensions(
        spreadsheet: Spreadsheet, dimension_range: DimensionRange,
        action: Annotated[Literal["group", "ungroup"], Field(description="Create an outline group or remove the specified outline level.")],
        sheet_id: SheetID = None, sheet_name: SheetName = None,
        depth: Annotated[int, Field(ge=1, description="Outline nesting level: 1 is outermost; larger numbers are deeper.")] = 1,
        group_state: Annotated[Literal["expand", "fold"] | None, Field(description="Initial expanded/collapsed state; only accepted for group, default expand.")] = None,
        dry_run: DryRun = False,
    ) -> dict:
        """Create/remove collapsible row or column outline groups at a nesting level.

        This is layout grouping, not aggregation of records. Inspect current groups
        before editing and verify with sheets_sheet_info afterwards.
        """
        _dimension_span(dimension_range)
        if action == "ungroup" and group_state is not None:
            raise ValueError("group_state only applies to group")
        return call("dim-" + action, {**target(spreadsheet, sheet_id, sheet_name), "range": dimension_range,
                    "depth": depth, "group-state": (group_state or "expand") if action == "group" else None}, write=True, dry_run=dry_run)

    @tool("Set complete spreadsheet freeze state", destructive=True)
    def sheets_freeze(
        spreadsheet: Spreadsheet,
        rows: Annotated[int, Field(ge=0, description="Number of leading rows to freeze; 0 unfreezes rows.")],
        columns: Annotated[int, Field(ge=0, description="Number of leading columns to freeze; 0 unfreezes columns.")],
        sheet_id: SheetID = None, sheet_name: SheetName = None, dry_run: DryRun = False,
    ) -> dict:
        """Replace the complete frozen-row and frozen-column state in one call.

        Both axes are required: to unfreeze rows while keeping two columns, pass
        rows=0, columns=2. Read sheets_sheet_info(include=["frozen"]) first if an
        existing axis must be preserved; this operation is not additive.
        """
        return call("dim-freeze", {**target(spreadsheet, sheet_id, sheet_name),
                    "rows": rows, "cols": columns}, write=True, dry_run=dry_run)

    @tool("Resize spreadsheet rows or columns", destructive=True)
    def sheets_resize_dimensions(
        spreadsheet: Spreadsheet,
        dimension: Annotated[Literal["rows", "columns"], Field(description="Resize row heights or column widths.")],
        sheet_id: SheetID = None, sheet_name: SheetName = None,
        dimension_range: Annotated[str | None, Field(description="Uniform mode interval: rows 2:10 or columns A:C. Required unless sizes is supplied.")] = None,
        size: Annotated[PixelSize | Literal["auto", "standard"] | None, Field(description="Uniform size in pixels, auto for rows only, or standard to restore defaults. Required with dimension_range.")] = None,
        sizes: Annotated[dict[str, PixelSize | Literal["auto", "standard"]] | None, Field(description='Per-interval size map, e.g. {"1":40,"2:10":"auto"} or {"A":120,"C:E":180}. Mutually exclusive with dimension_range/size. Column auto is unsupported.')] = None,
        dry_run: DryRun = False,
    ) -> dict:
        """Set uniform or per-interval row heights/column widths in pixels.

        Use either dimension_range+size or sizes, never both. Only rows support
        automatic fitting. Map intervals must not overlap. Inspect existing sizes
        first and verify row_heights/col_widths afterwards; do not resize unrelated cells.
        """
        options = target(spreadsheet, sheet_id, sheet_name)
        if sizes is not None:
            if not sizes or dimension_range is not None or size is not None:
                raise ValueError("sizes must be nonempty and excludes dimension_range/size")
            spans = _nonoverlapping_dimensions(list(sizes))
            values = sizes.values()
            options["heights" if dimension == "rows" else "widths"] = encode(sizes)
        else:
            if dimension_range is None or size is None:
                raise ValueError("supply dimension_range+size or sizes")
            spans = [_dimension_span(dimension_range)]
            values = [size]
            options.update({"range": dimension_range})
            if isinstance(size, int):
                options["height" if dimension == "rows" else "width"] = size
            else:
                options["type"] = size
        if any(span[0] != dimension for span in spans):
            raise ValueError("range coordinates must match dimension")
        if dimension == "columns" and "auto" in values:
            raise ValueError("automatic fitting is supported for rows only")
        return call("rows-resize" if dimension == "rows" else "cols-resize", options, write=True, dry_run=dry_run)

    @tool("Merge or unmerge spreadsheet cells", destructive=True, idempotent=False)
    def sheets_merge_cells(
        spreadsheet: Spreadsheet, cell_range: A1Range,
        action: Annotated[Literal["merge", "unmerge"], Field(description="Merge cells or remove all merges within the supplied range.")],
        sheet_id: SheetID = None, sheet_name: SheetName = None,
        merge_type: Annotated[Literal["all", "rows", "columns"] | None, Field(description="Merge the whole rectangle, separately by row, or separately by column. Only for merge; omitted means all.")] = None,
        dry_run: DryRun = False,
    ) -> dict:
        """Merge a rectangle or unmerge all merged cells within a range.

        Merging keeps only each resulting area's top-left content and clears the
        other cells. Read layout and target values/formulas/notes/validation first;
        do not merge occupied non-anchor cells or overlap existing merges. For many
        merges prefer a single layout specification. Verify merges and anchor values.
        """
        if action == "unmerge" and merge_type is not None:
            raise ValueError("merge_type only applies to merge")
        return call("cells-" + action, {**target(spreadsheet, sheet_id, sheet_name), "range": cell_range,
                    "merge-type": (merge_type or "all") if action == "merge" else None}, write=True, dry_run=dry_run)

    @tool("Copy a spreadsheet range", destructive=True, idempotent=False)
    def sheets_copy_range(
        spreadsheet: Spreadsheet, source_range: A1Range,
        target_range: Annotated[str, Field(min_length=1, description="Destination A1 range or anchor cell, e.g. F2; extent is inferred from the source.")],
        sheet_id: SheetID = None, sheet_name: SheetName = None,
        target_sheet_id: Annotated[str | None, Field(description="Destination sub-sheet ID in the same workbook; omit to use the source sub-sheet.")] = None,
        paste_type: Annotated[Literal["values", "formulas", "formats", "all"], Field(description="What to copy; formats inherits styling without replacing values. Row heights/column widths are separate.")] = "all",
        dry_run: DryRun = False,
    ) -> dict:
        """Copy a range's values, formulas, formats or all into the same workbook.

        Inspect source and destination before overwriting; copying formats is useful
        before extending an existing table. Verify the destination afterwards.
        """
        return call("range-copy", {**target(spreadsheet, sheet_id, sheet_name), "source-range": source_range,
                    "target-range": target_range, "target-sheet-id": target_sheet_id, "paste-type": paste_type}, write=True, dry_run=dry_run)

    @tool("Move a spreadsheet range", destructive=True, idempotent=False)
    def sheets_move_range(
        spreadsheet: Spreadsheet, source_range: A1Range,
        target_range: Annotated[str, Field(min_length=1, description="Destination A1 range or anchor cell; extent is inferred from the source.")],
        sheet_id: SheetID = None, sheet_name: SheetName = None,
        target_sheet_id: Annotated[str | None, Field(description="Destination sub-sheet ID in the same workbook; omit for the source sub-sheet.")] = None,
        dry_run: DryRun = False,
    ) -> dict:
        """Cut and move a cell range within the same workbook, optionally across tabs.

        The source changes and destination content can be overwritten. Inspect both
        ranges and affected references first. Verify both after moving; retrying the
        original call after a partial failure can move different or empty content.
        """
        return call("range-move", {**target(spreadsheet, sheet_id, sheet_name), "source-range": source_range,
                    "target-range": target_range, "target-sheet-id": target_sheet_id}, write=True, dry_run=dry_run)

    @tool("Autofill a spreadsheet range", destructive=True, idempotent=False)
    def sheets_fill_range(
        spreadsheet: Spreadsheet, source_range: A1Range, target_range: A1Range,
        sheet_id: SheetID = None, sheet_name: SheetName = None,
        series_type: Annotated[Literal["auto", "linear", "growth", "date", "copy"], Field(description="Infer a series, extend linearly, extend geometrically, extend dates, or copy the seed pattern.")] = "auto",
        dry_run: DryRun = False,
    ) -> dict:
        """Autofill a target range from seed cells in the same sub-sheet.

        Inspect the seed values and target first. Verify first/middle/last cells and
        run sheets_verify_formulas when the fill writes formulas.
        """
        return call("range-fill", {**target(spreadsheet, sheet_id, sheet_name), "source-range": source_range,
                    "target-range": target_range, "series-type": series_type}, write=True, dry_run=dry_run)

    @tool("Sort spreadsheet records by columns", destructive=True, idempotent=False)
    def sheets_sort_range(
        spreadsheet: Spreadsheet, cell_range: A1Range,
        sort_keys: Annotated[list[SortKey], Field(min_length=1, description="Sort columns in priority order; every column must belong to cell_range.")],
        sheet_id: SheetID = None, sheet_name: SheetName = None,
        has_header: Annotated[bool, Field(description="True excludes the first row of cell_range from sorting; False sorts every row.")] = False,
        dry_run: DryRun = False,
    ) -> dict:
        """Sort whole records atomically within a range using one or more columns.

        cell_range must cover the complete record width; a sort key alone does not
        define the range. Read sample values/types first: numeric-looking text sorts
        lexically. Do not simulate sorting by reading values and overwriting them.
        Verify first/middle/last rows retain their original column relationships.
        """
        bounds = re.fullmatch(r"([A-Z]+)(?:[1-9][0-9]*)?:([A-Z]+)(?:[1-9][0-9]*)?", cell_range)
        if bounds:
            first, last = map(_column_number, bounds.groups())
            if first > last or any(not first <= _column_number(key.column) <= last for key in sort_keys):
                raise ValueError("sort key columns must be within cell_range")
        return call("range-sort", {**target(spreadsheet, sheet_id, sheet_name), "range": cell_range,
                    "sort-keys": encode(sort_keys), "has-header": has_header}, write=True, dry_run=dry_run)

    @tool("Find spreadsheet cell text", read=True)
    def sheets_search_cells(
        spreadsheet: Spreadsheet,
        find: Annotated[str, Field(min_length=1, description="Literal text to locate, or a CLI-supported regular expression when regex=True.")],
        sheet_id: SheetID = None, sheet_name: SheetName = None,
        cell_range: Annotated[str | None, Field(description="Optional A1 search range; omitted searches the whole selected sub-sheet.")] = None,
        match_case: Annotated[bool, Field(description="True makes text matching case-sensitive.")] = False,
        match_entire_cell: Annotated[bool, Field(description="True requires the entire cell content to match.")] = False,
        regex: Annotated[bool, Field(description="Interpret find as a CLI-supported regular expression; False treats find literally.")] = False,
        include_formulas: Annotated[bool, Field(description="Also search formula source text, in addition to cell values.")] = False,
        max_matches: Annotated[int, Field(ge=1, le=5000, description="Maximum matches in one page; bounded by this wrapper to 5000.")] = 100,
        offset: Annotated[int, Field(ge=0, description="Number of matches to skip; continue using data.next_offset when data.has_more is true.")] = 0,
        dry_run: DryRun = False,
    ) -> dict:
        """Find cells containing text and return addresses without downloading a whole sheet.

        data contains matches (address, value, sheet_id), total_matches, has_more and
        next_offset. Follow pagination for complete results. Use sheets_read to
        understand table structure; use this tool only to locate specified text.
        """
        return call("cells-search", {**target(spreadsheet, sheet_id, sheet_name), "find": find,
                    "range": cell_range, "match-case": match_case, "match-entire-cell": match_entire_cell,
                    "regex": regex, "include-formulas": include_formulas, "max-matches": max_matches,
                    "offset": offset}, dry_run=dry_run)

    @tool("Replace spreadsheet cell text", destructive=True, idempotent=False)
    def sheets_replace_cells(
        spreadsheet: Spreadsheet,
        find: Annotated[str, Field(min_length=1, description="Literal text to replace, or a CLI-supported regular expression when regex=True.")],
        replacement: Annotated[str, Field(description="Replacement text; empty string deletes matches. In regex mode use $1, $2 for capture groups.")],
        sheet_id: SheetID = None, sheet_name: SheetName = None,
        cell_range: Annotated[str | None, Field(description="Optional A1 replacement range. Omitting it modifies matches across the whole selected sub-sheet.")] = None,
        match_case: Annotated[bool, Field(description="True makes matching case-sensitive.")] = False,
        match_entire_cell: Annotated[bool, Field(description="True replaces only whole-cell matches.")] = False,
        regex: Annotated[bool, Field(description="Interpret find as a CLI-supported regular expression; False treats it literally.")] = False,
        include_formulas: Annotated[bool, Field(description="Also modify formula source text; enable only when formula edits are intended.")] = False,
        dry_run: DryRun = False,
    ) -> dict:
        """Replace matching cell text within one sub-sheet, optionally including formulas.

        First inspect matches with sheets_search_cells. dry_run previews the request
        and CLI replacement estimate. After execution search for remaining old text
        and read representative changed cells. Replacement is not generally idempotent
        (e.g. a -> aa); inspect state before retrying. Verify edited formulas separately.
        """
        return call("cells-replace", {**target(spreadsheet, sheet_id, sheet_name), "find": find,
                    "replacement": replacement, "range": cell_range, "match-case": match_case,
                    "match-entire-cell": match_entire_cell, "regex": regex,
                    "include-formulas": include_formulas}, write=True, dry_run=dry_run)

    @tool("List conditional-format rules", read=True)
    def sheets_list_conditional_formats(
        spreadsheet: Spreadsheet, sheet_id: SheetID = None, sheet_name: SheetName = None,
        rule_id: Annotated[str | None, Field(description="Optional rule ID filter; omit to list every conditional-format rule in this sub-sheet.")] = None,
    ) -> dict:
        """Read conditional-format rule IDs and complete configurations for one sub-sheet.

        Read before updating/deleting rules. Creating a rule does not prove its style
        is effective: verify the listed definition and then use
        sheets_get_conditional_format_result to inspect representative cell styles.
        """
        return call("cond-format-list", {**target(spreadsheet, sheet_id, sheet_name), "rule-id": rule_id})

    @tool("Read computed conditional-format styles", read=True)
    def sheets_get_conditional_format_result(
        spreadsheet: Spreadsheet, cell_range: A1Range, sheet_id: SheetID = None,
        sheet_name: SheetName = None, max_chars: MaxChars = 25000,
    ) -> dict:
        """Read final computed cell styles in a range, including conditional formatting.

        Returns cell_styles and range/truncation metadata, not cell values, formula
        text, a rule ID or an explicit hit flag. Sample cells that should match and
        should not match, including blanks/boundaries. Use sheets_read for values.
        """
        return call("cond-format-result-get", {**target(spreadsheet, sheet_id, sheet_name),
                    "range": cell_range, "max-chars": max_chars})

    @tool("Create or replace a conditional-format rule", destructive=True, idempotent=False)
    def sheets_write_conditional_format(
        spreadsheet: Spreadsheet,
        action: Annotated[Literal["create", "update"], Field(description="Create a new rule, or completely replace the rule identified by rule_id.")],
        rule: ConditionalRule,
        sheet_id: SheetID = None, sheet_name: SheetName = None,
        rule_id: Annotated[str | None, Field(description="Required only for update; obtain from sheets_list_conditional_formats.")] = None,
        dry_run: DryRun = False,
    ) -> dict:
        """Create a dynamic conditional-format rule or replace an existing rule in full.

        rule_type chooses the attrs schema. Example: {"rule_type":"cellIs",
        "ranges":["B2:B100"],"style":{"back_color":"#FF0000"},
        "attrs":[{"compare_type":"greaterThan","value":"100"}]}.
        Update requires the complete desired rule: read it first and preserve settings.
        Inspect sample data before choosing columns/types; date formulas must exclude
        blanks. After writing, verify the rule with sheets_list_conditional_formats
        AND verify computed styles with sheets_get_conditional_format_result.
        Creation is not idempotent: inspect existing rules before retrying uncertain results.
        """
        if (action == "update") != bool(rule_id):
            raise ValueError("rule_id is required only for update")
        payload = rule.model_dump(exclude_unset=True)
        # Literal defaults are not used: discriminated rule_type is required in input.
        kind, ranges = payload.pop("rule_type"), payload.pop("ranges")
        return call("cond-format-" + action, {**target(spreadsheet, sheet_id, sheet_name),
                    "rule-id": rule_id, "rule-type": kind, "ranges": encode(ranges),
                    "properties": encode(payload)}, write=True, dry_run=dry_run)

    @tool("Delete a conditional-format rule", destructive=True, idempotent=False)
    def sheets_delete_conditional_format(
        spreadsheet: Spreadsheet, rule_id: RuleID, sheet_id: SheetID = None,
        sheet_name: SheetName = None, confirmed: Confirmed = False, dry_run: DryRun = False,
    ) -> dict:
        """Delete one conditional-format rule by ID; other rules and cell values remain.

        Read the target rule first. Execute with confirmed=True after user
        authorization, or use dry_run to preview without deleting. Verify the rule ID
        is absent from sheets_list_conditional_formats afterwards.
        """
        if not (confirmed or dry_run):
            raise ValueError("delete requires confirmed=True or dry_run=True")
        return call("cond-format-delete", {**target(spreadsheet, sheet_id, sheet_name),
                    "rule-id": rule_id}, write=True, confirmed=confirmed, dry_run=dry_run)
