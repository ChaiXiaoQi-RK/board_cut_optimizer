#!/usr/bin/env python3
"""
Board cutting optimizer.

Reads a CSV/XLSX cutting list with columns:
length,width,thickness,quantity

All dimensions are millimeters.
"""

from __future__ import annotations

import argparse
import csv
import html
import math
import os
import sys
from dataclasses import dataclass, replace
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


EDGE_MARGIN = 10.0
MIN_KERF = 4.0
MAX_KERF = 10.0
ROUND_EPSILON = 1e-9
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


@dataclass(frozen=True)
class Part:
    source_row: int
    item_no: int
    length: float
    width: float
    thickness: float
    area: float


@dataclass(frozen=True)
class FreeRect:
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class Placement:
    part: Part
    sheet_no: int
    x: float
    y: float
    length: float
    width: float
    rotated: bool
    row_no: int = 0
    x_gap_after: float = 0.0
    y_gap_after: float = 0.0


@dataclass
class Sheet:
    sheet_no: int
    thickness: float
    placements: List[Placement]
    free_rects: List[FreeRect]

    @property
    def used_area(self) -> float:
        return sum(p.length * p.width for p in self.placements)


@dataclass(frozen=True)
class ThicknessResult:
    thickness: float
    sheets: List[Sheet]
    sheet_equivalent: float
    integer_sheets: int
    total_weight_kg: float


def normalize_header(value: object) -> str:
    text = str(value or "").strip().lower()
    aliases = {
        "thickness": "thickness",
        "thickness_mm": "thickness",
        "weight": "weight",
        "weight_per_m2": "weight",
        "kg_per_m2": "weight",
        "kg/m2": "weight",
        "kg/㎡": "weight",
        "\u539a\u5ea6": "thickness",
        "\u91cd\u91cf": "weight",
        "\u6bcf\u5e73\u65b9\u91cd\u91cf": "weight",
        "\u6bcf\u5e73\u7c73\u91cd\u91cf": "weight",
        "\u6bcf\u5e73\u7c73kg": "weight",
        "\u957f\u5ea6": "length",
        "\u5bbd\u5ea6": "width",
        "\u6570\u91cf": "quantity",
    }
    return aliases.get(text, text)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Calculate board cutting layout and sheet usage."
    )
    parser.add_argument("--board-length", type=float, required=True, help="Full board length in mm.")
    parser.add_argument("--board-width", type=float, required=True, help="Full board width in mm.")
    parser.add_argument("--input", required=True, help="CSV or XLSX cutting list.")
    parser.add_argument("--output", help="Optional CSV path for detailed placements.")
    parser.add_argument("--report-svg", help="Optional SVG report path with layout diagrams.")
    parser.add_argument("--image-output", help="Optional PNG long screenshot report path.")
    parser.add_argument("--image-width", type=int, default=1080, help="Report image width in pixels.")
    return parser.parse_args(argv)


def default_report_png_path(input_path: str) -> str:
    directory = os.path.dirname(os.path.abspath(input_path))
    base_name = os.path.splitext(os.path.basename(input_path))[0]
    return os.path.join(directory, f"{base_name}.png")


def parse_positive_float(value: object, field: str, row_no: int) -> float:
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError(f"Row {row_no}: {field} must be a number, got {value!r}.")
    if parsed <= 0:
        raise ValueError(f"Row {row_no}: {field} must be greater than 0, got {parsed}.")
    return parsed


def parse_positive_int(value: object, field: str, row_no: int) -> int:
    raw = str(value).strip()
    try:
        parsed_float = float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"Row {row_no}: {field} must be a positive integer, got {value!r}.")
    parsed = int(parsed_float)
    if parsed <= 0 or abs(parsed - parsed_float) > ROUND_EPSILON:
        raise ValueError(f"Row {row_no}: {field} must be a positive integer, got {value!r}.")
    return parsed


def read_csv(path: str) -> List[Dict[str, object]]:
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError("Input CSV has no header row.")
        reader.fieldnames = [normalize_header(name) for name in reader.fieldnames]
        return list(reader)


def read_xlsx(path: str) -> List[Dict[str, object]]:
    try:
        import openpyxl  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "Reading .xlsx requires openpyxl. Install it with: pip install openpyxl"
        ) from exc

    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    sheet = workbook.active
    rows = sheet.iter_rows(values_only=True)
    try:
        headers = [normalize_header(value) for value in next(rows)]
    except StopIteration:
        raise ValueError("Input XLSX is empty.")

    records: List[Dict[str, object]] = []
    for values in rows:
        if not values or all(value is None or str(value).strip() == "" for value in values):
            continue
        records.append({header: value for header, value in zip(headers, values)})
    return records


def read_parts(path: str) -> List[Part]:
    extension = os.path.splitext(path)[1].lower()
    if extension == ".csv":
        rows = read_csv(path)
    elif extension == ".xlsx":
        rows = read_xlsx(path)
    else:
        raise ValueError("Input file must be .csv or .xlsx.")

    required = {"length", "width", "thickness", "quantity"}
    if rows:
        missing = required - set(rows[0].keys())
    else:
        missing = set()
    if missing:
        raise ValueError(f"Input is missing required columns: {', '.join(sorted(missing))}.")

    parts: List[Part] = []
    next_item_no = 1
    for index, row in enumerate(rows, start=2):
        if not row or all(str(row.get(column, "") or "").strip() == "" for column in required):
            continue
        length = parse_positive_float(row.get("length"), "length", index)
        width = parse_positive_float(row.get("width"), "width", index)
        thickness = parse_positive_float(row.get("thickness"), "thickness", index)
        quantity = parse_positive_int(row.get("quantity"), "quantity", index)
        for _ in range(quantity):
            parts.append(
                Part(
                    source_row=index,
                    item_no=next_item_no,
                    length=length,
                    width=width,
                    thickness=thickness,
                    area=length * width,
                )
            )
            next_item_no += 1

    if not parts:
        raise ValueError("Input contains no parts.")
    return parts


def discover_weight_table_path() -> Optional[str]:
    candidates = []
    for name in os.listdir(SCRIPT_DIR):
        lower = name.lower()
        if not lower.endswith(".csv"):
            continue
        if "weight" in lower or "\u91cd\u91cf" in name:
            candidates.append(os.path.join(SCRIPT_DIR, name))
    return sorted(candidates)[0] if candidates else None


def load_weight_table(path: str) -> Dict[float, float]:
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"Weight table has no header row: {path}")
        reader.fieldnames = [normalize_header(value) for value in reader.fieldnames]
        required = {"thickness", "weight"}
        missing = required - set(reader.fieldnames)
        if missing:
            raise ValueError(f"Weight table is missing columns: {', '.join(sorted(missing))}")
        table: Dict[float, float] = {}
        for row_no, row in enumerate(reader, start=2):
            if not row:
                continue
            thickness_raw = str(row.get("thickness", "")).strip()
            weight_raw = str(row.get("weight", "")).strip()
            if not thickness_raw or not weight_raw:
                continue
            thickness = float(thickness_raw)
            weight = float(weight_raw)
            if thickness <= 0 or weight <= 0:
                raise ValueError(f"Weight table row {row_no} must be positive.")
            table[thickness] = weight
    if not table:
        raise ValueError(f"Weight table contains no usable rows: {path}")
    return table


def weight_for_thickness(thickness: float, weight_table: Optional[Dict[float, float]]) -> Optional[float]:
    if not weight_table:
        return None
    for key, value in weight_table.items():
        if abs(key - thickness) < ROUND_EPSILON:
            return value
    return None


def ceil_to_tenth(value: float) -> float:
    return math.ceil((value - ROUND_EPSILON) * 10.0) / 10.0


def fmt_number(value: float) -> str:
    rounded = round(value, 1)
    if abs(rounded - int(rounded)) < ROUND_EPSILON:
        return str(int(rounded))
    return f"{rounded:.1f}"


def orientation_options(part: Part) -> List[Tuple[float, float, bool]]:
    normal = (part.length, part.width, False)
    rotated = (part.width, part.length, True)
    if abs(part.length - part.width) < ROUND_EPSILON:
        return [normal]
    return [normal, rotated]


def fits_in_available_area(part: Part, available_width: float, available_height: float) -> bool:
    return any(
        width <= available_width + ROUND_EPSILON and height <= available_height + ROUND_EPSILON
        for width, height, _ in orientation_options(part)
    )


def sort_free_rects(free_rects: Iterable[FreeRect]) -> List[FreeRect]:
    return sorted(free_rects, key=lambda rect: (rect.y, rect.x, rect.height, rect.width))


def prune_free_rects(free_rects: Iterable[FreeRect]) -> List[FreeRect]:
    cleaned = [
        rect
        for rect in free_rects
        if rect.width > ROUND_EPSILON and rect.height > ROUND_EPSILON
    ]
    result: List[FreeRect] = []
    for index, rect in enumerate(cleaned):
        contained = False
        for other_index, other in enumerate(cleaned):
            if index == other_index:
                continue
            if (
                rect.x >= other.x - ROUND_EPSILON
                and rect.y >= other.y - ROUND_EPSILON
                and rect.x + rect.width <= other.x + other.width + ROUND_EPSILON
                and rect.y + rect.height <= other.y + other.height + ROUND_EPSILON
            ):
                contained = True
                break
        if not contained:
            result.append(rect)
    return sort_free_rects(result)


def split_free_rect(
    rect: FreeRect,
    placed_width: float,
    placed_height: float,
    available_width: float,
    available_height: float,
) -> List[FreeRect]:
    occupied_width = placed_width + MIN_KERF
    occupied_height = placed_height + MIN_KERF
    right_width = rect.width - occupied_width
    bottom_height = rect.height - occupied_height

    new_rects: List[FreeRect] = []
    if right_width > ROUND_EPSILON:
        new_rects.append(
            FreeRect(
                x=rect.x + occupied_width,
                y=rect.y,
                width=right_width,
                height=placed_height,
            )
        )
    if bottom_height > ROUND_EPSILON:
        new_rects.append(
            FreeRect(
                x=rect.x,
                y=rect.y + occupied_height,
                width=rect.width,
                height=bottom_height,
            )
        )

    clipped: List[FreeRect] = []
    for candidate in new_rects:
        max_width = available_width - candidate.x
        max_height = available_height - candidate.y
        clipped.append(
            FreeRect(
                x=candidate.x,
                y=candidate.y,
                width=min(candidate.width, max_width),
                height=min(candidate.height, max_height),
            )
        )
    return clipped


def find_position(
    sheet: Sheet,
    part: Part,
    available_width: float,
    available_height: float,
) -> Optional[Tuple[int, float, float, bool]]:
    best: Optional[Tuple[float, float, float, float, int, float, float, bool]] = None
    for rect_index, rect in enumerate(sheet.free_rects):
        for placed_width, placed_height, rotated in orientation_options(part):
            if (
                placed_width <= rect.width + ROUND_EPSILON
                and placed_height <= rect.height + ROUND_EPSILON
            ):
                waste = rect.width * rect.height - placed_width * placed_height
                short_side_leftover = min(rect.width - placed_width, rect.height - placed_height)
                score = (waste, short_side_leftover, rect.y, rect.x, rect_index, placed_width, placed_height)
                if best is None or score < best[:7]:
                    best = (
                        waste,
                        short_side_leftover,
                        rect.y,
                        rect.x,
                        rect_index,
                        placed_width,
                        placed_height,
                        rotated,
                    )
    if best is None:
        return None
    _, _, _, _, rect_index, placed_width, placed_height, rotated = best
    return rect_index, placed_width, placed_height, rotated


def place_on_sheet(
    sheet: Sheet,
    part: Part,
    available_width: float,
    available_height: float,
) -> bool:
    position = find_position(sheet, part, available_width, available_height)
    if position is None:
        return False

    rect_index, placed_width, placed_height, rotated = position
    rect = sheet.free_rects.pop(rect_index)
    sheet.placements.append(
        Placement(
            part=part,
            sheet_no=sheet.sheet_no,
            x=EDGE_MARGIN + rect.x,
            y=EDGE_MARGIN + rect.y,
            length=placed_width,
            width=placed_height,
            rotated=rotated,
        )
    )
    sheet.free_rects.extend(split_free_rect(rect, placed_width, placed_height, available_width, available_height))
    sheet.free_rects = prune_free_rects(sheet.free_rects)
    return True


def pack_ordered_parts(
    thickness: float,
    ordered_parts: Sequence[Part],
    available_width: float,
    available_height: float,
) -> List[Sheet]:
    sheets: List[Sheet] = []
    for part in ordered_parts:
        placed = False
        for sheet in sheets:
            if place_on_sheet(sheet, part, available_width, available_height):
                placed = True
                break
        if not placed:
            sheet = Sheet(
                sheet_no=len(sheets) + 1,
                thickness=thickness,
                placements=[],
                free_rects=[FreeRect(0.0, 0.0, available_width, available_height)],
            )
            if not place_on_sheet(sheet, part, available_width, available_height):
                raise RuntimeError("Unexpected packing failure after fit validation.")
            sheets.append(sheet)
    return sheets


def sorted_part_strategies(parts: Sequence[Part]) -> List[List[Part]]:
    strategies = [
        sorted(
            parts,
            key=lambda part: (part.area, max(part.length, part.width), min(part.length, part.width), -part.item_no),
            reverse=True,
        ),
        sorted(
            parts,
            key=lambda part: (max(part.length, part.width), part.area, min(part.length, part.width), -part.item_no),
            reverse=True,
        ),
        sorted(
            parts,
            key=lambda part: (min(part.length, part.width), part.area, max(part.length, part.width), -part.item_no),
            reverse=True,
        ),
        sorted(
            parts,
            key=lambda part: (part.length, part.width, part.area, -part.item_no),
            reverse=True,
        ),
        sorted(
            parts,
            key=lambda part: (part.width, part.length, part.area, -part.item_no),
            reverse=True,
        ),
    ]

    unique: List[List[Part]] = []
    seen = set()
    for strategy in strategies:
        signature = tuple(part.item_no for part in strategy)
        if signature not in seen:
            unique.append(strategy)
            seen.add(signature)
    return unique


def repack_sheet_with_even_gaps(
    sheet: Sheet,
    board_length: float,
    board_width: float,
) -> Sheet:
    rows: Dict[float, List[Placement]] = {}
    for placement in sheet.placements:
        rows.setdefault(placement.y, []).append(placement)

    sorted_rows = sorted(rows.items(), key=lambda item: item[0])
    row_heights = [max(placement.width for placement in placements) for _, placements in sorted_rows]
    used_height_with_min_kerf = sum(row_heights) + MIN_KERF * max(0, len(row_heights) - 1)
    available_height = board_width - EDGE_MARGIN * 2.0
    extra_y_gap = 0.0
    if len(row_heights) > 1:
        extra_y_gap = max(0.0, available_height - used_height_with_min_kerf) / (len(row_heights) - 1)

    new_placements: List[Placement] = []
    cursor_y = EDGE_MARGIN
    for row_index, (_, placements) in enumerate(sorted_rows, start=1):
        row = sorted(placements, key=lambda placement: placement.x)
        row_width = sum(placement.length for placement in row)
        gaps = max(0, len(row) - 1)
        used_width_with_min_kerf = row_width + MIN_KERF * gaps
        available_width = board_length - EDGE_MARGIN * 2.0
        extra_x_gap = 0.0
        if gaps:
            extra_x_gap = max(0.0, available_width - used_width_with_min_kerf) / gaps

        cursor_x = EDGE_MARGIN
        actual_x_gap = min(MAX_KERF, MIN_KERF + extra_x_gap) if gaps else 0.0
        actual_y_gap = min(MAX_KERF, MIN_KERF + extra_y_gap) if len(row_heights) > 1 else 0.0
        for placement_index, placement in enumerate(row):
            new_placements.append(
                replace(
                    placement,
                    x=cursor_x,
                    y=cursor_y,
                    row_no=row_index,
                    x_gap_after=actual_x_gap if placement_index < len(row) - 1 else 0.0,
                    y_gap_after=actual_y_gap if row_index < len(row_heights) else 0.0,
                )
            )
            cursor_x += placement.length + actual_x_gap
        cursor_y += row_heights[row_index - 1] + actual_y_gap

    return Sheet(
        sheet_no=sheet.sheet_no,
        thickness=sheet.thickness,
        placements=sorted(new_placements, key=lambda p: (p.sheet_no, p.row_no, p.x)),
        free_rects=[],
    )


def pack_parts_for_thickness(
    thickness: float,
    parts: Sequence[Part],
    board_length: float,
    board_width: float,
    weight_table: Optional[Dict[float, float]] = None,
) -> ThicknessResult:
    available_width = board_length - EDGE_MARGIN * 2.0
    available_height = board_width - EDGE_MARGIN * 2.0
    if available_width <= 0 or available_height <= 0:
        raise ValueError("Board dimensions must be larger than the 10mm margins on all sides.")

    for part in parts:
        if not fits_in_available_area(part, available_width, available_height):
            raise ValueError(
                "Part cannot fit inside usable board area: "
                f"row {part.source_row}, item {part.item_no}, "
                f"{fmt_number(part.length)}x{fmt_number(part.width)}x{fmt_number(part.thickness)}mm. "
                f"Usable area is {fmt_number(available_width)}x{fmt_number(available_height)}mm."
            )

    candidates = [
        pack_ordered_parts(thickness, ordered_parts, available_width, available_height)
        for ordered_parts in sorted_part_strategies(parts)
    ]
    sheets = min(
        candidates,
        key=lambda candidate: (
            len(candidate),
            candidate[-1].used_area if candidate else 0.0,
            sum(sheet.used_area for sheet in candidate),
        ),
    )
    final_sheets = [repack_sheet_with_even_gaps(sheet, board_length, board_width) for sheet in sheets]
    sheet_area = board_length * board_width
    if len(final_sheets) == 1:
        equivalent = max(0.1, ceil_to_tenth(final_sheets[0].used_area / sheet_area))
    else:
        full_sheets = len(final_sheets) - 1
        last_sheet_fraction = max(0.1, ceil_to_tenth(final_sheets[-1].used_area / sheet_area))
        equivalent = full_sheets + last_sheet_fraction

    full_sheet_weight_kg = weight_for_thickness(thickness, weight_table)
    total_weight_kg = 0.0
    if full_sheet_weight_kg is not None:
        total_weight_kg = equivalent * full_sheet_weight_kg

    return ThicknessResult(
        thickness=thickness,
        sheets=final_sheets,
        sheet_equivalent=equivalent,
        integer_sheets=len(final_sheets),
        total_weight_kg=total_weight_kg,
    )


def group_by_thickness(parts: Sequence[Part]) -> Dict[float, List[Part]]:
    grouped: Dict[float, List[Part]] = {}
    for part in parts:
        grouped.setdefault(part.thickness, []).append(part)
    return dict(sorted(grouped.items(), key=lambda item: item[0]))


def write_output_csv(path: str, results: Sequence[ThicknessResult]) -> None:
    fieldnames = [
        "thickness",
        "sheet_no",
        "row_no",
        "item_no",
        "source_row",
        "x_mm",
        "y_mm",
        "length_mm",
        "width_mm",
        "rotated",
        "x_gap_after_mm",
        "y_gap_after_mm",
    ]
    with open(path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for result in results:
            for sheet in result.sheets:
                for placement in sheet.placements:
                    writer.writerow(
                        {
                            "thickness": fmt_number(result.thickness),
                            "sheet_no": sheet.sheet_no,
                            "row_no": placement.row_no,
                            "item_no": placement.part.item_no,
                            "source_row": placement.part.source_row,
                            "x_mm": f"{placement.x:.1f}",
                            "y_mm": f"{placement.y:.1f}",
                            "length_mm": f"{placement.length:.1f}",
                            "width_mm": f"{placement.width:.1f}",
                            "rotated": "yes" if placement.rotated else "no",
                            "x_gap_after_mm": f"{placement.x_gap_after:.1f}",
                            "y_gap_after_mm": f"{placement.y_gap_after:.1f}",
                        }
                    )


def svg_text(
    x: float,
    y: float,
    value: object,
    size: int = 26,
    fill: str = "#1f2937",
    weight: int = 400,
    anchor: str = "start",
) -> str:
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" '
        f'font-family="Microsoft YaHei, SimHei, Arial, sans-serif" '
        f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}">'
        f"{html.escape(str(value))}</text>"
    )


def svg_rect(
    x: float,
    y: float,
    width: float,
    height: float,
    fill: str,
    stroke: str = "none",
    stroke_width: float = 1.0,
    radius: float = 8.0,
    extra: str = "",
) -> str:
    return (
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{width:.1f}" height="{height:.1f}" '
        f'rx="{radius:.1f}" ry="{radius:.1f}" fill="{fill}" '
        f'stroke="{stroke}" stroke-width="{stroke_width:.1f}" {extra}/>'
    )


def aggregate_parts(placements: Sequence[Placement]) -> List[Tuple[float, float, int, float]]:
    grouped: Dict[Tuple[float, float], Tuple[int, float]] = {}
    for placement in placements:
        key = (placement.part.length, placement.part.width)
        quantity, area = grouped.get(key, (0, 0.0))
        grouped[key] = (quantity + 1, area + placement.part.area)
    rows = [
        (length, width, quantity, area)
        for (length, width), (quantity, area) in grouped.items()
    ]
    return sorted(rows, key=lambda row: (row[0] * row[1], row[0], row[1]), reverse=True)


def compute_diagram_geometry(
    board_length: float,
    board_width: float,
    max_width: float,
    max_height: float,
) -> Tuple[float, float, float]:
    scale = min(max_width / board_length, max_height / board_width)
    return board_length * scale, board_width * scale, scale


def split_placements_for_side_columns(placements: Sequence[Placement]) -> Tuple[List[Placement], List[Placement]]:
    midpoint = (len(placements) + 1) // 2
    return list(placements[:midpoint]), list(placements[midpoint:])


def build_report_card(
    title: str,
    subtitle: str,
    result: ThicknessResult,
    board_length: float,
    board_width: float,
    card_width: int,
) -> Tuple[float, List[str]]:
    elements: List[str] = []
    y = 42.0
    elements.append(svg_text(28, y, title, size=34, weight=700, fill="#111827"))
    y += 36
    elements.append(svg_text(28, y, subtitle, size=23, fill="#6b7280"))
    y += 44
    if result.total_weight_kg > 0:
        elements.append(svg_text(28, y, f"\u603b\u91cd\u91cf {result.total_weight_kg:.1f} kg", size=24, weight=700, fill="#111827"))
        y += 34

    placements = [placement for sheet in result.sheets for placement in sheet.placements]
    elements.append(svg_text(28, y, "\u96f6\u5207\u6e05\u5355\u6c47\u603b", size=28, weight=700, fill="#111827"))
    y += 38
    elements.append(svg_text(28, y, "\u5c3a\u5bf8(mm)", size=22, weight=700, fill="#374151"))
    elements.append(svg_text(280, y, "\u6570\u91cf", size=22, weight=700, fill="#374151"))
    elements.append(svg_text(430, y, "\u9762\u79ef(mm\u00b2)", size=22, weight=700, fill="#374151"))
    y += 14
    elements.append(svg_rect(24, y, card_width - 48, 1, "#e5e7eb", radius=0))
    y += 30
    for length, width, quantity, area in aggregate_parts(placements):
        elements.append(svg_text(28, y, f"{fmt_number(length)} x {fmt_number(width)}", size=22))
        elements.append(svg_text(280, y, quantity, size=22))
        elements.append(svg_text(430, y, f"{area:.1f}", size=22))
        y += 34

    palette = ["#2563eb", "#059669", "#dc2626", "#7c3aed", "#ea580c", "#0891b2", "#be123c"]
    side_panel_width = 150.0
    side_gap = 14.0
    max_diagram_width = card_width - 56 - side_panel_width * 2.0 - side_gap * 2.0
    max_diagram_height = 680.0

    for sheet in result.sheets:
        y += 24
        sheet_fraction = ceil_to_tenth(sheet.used_area / (board_length * board_width))
        elements.append(
            svg_text(
                28,
                y,
                f"\u6574\u677f {sheet.sheet_no}\uff1a{len(sheet.placements)}\u5757\uff0c\u9762\u79ef\u6298\u7b97 {sheet_fraction:.1f}\u5f20",
                size=28,
                weight=700,
                fill="#111827",
            )
        )
        y += 24
        diagram_width, diagram_height, scale = compute_diagram_geometry(
            board_length,
            board_width,
            max_diagram_width,
            max_diagram_height,
        )
        diagram_x = 28.0 + side_panel_width + side_gap + (max_diagram_width - diagram_width) / 2.0
        diagram_y = y
        left_x = 28.0
        right_x = diagram_x + diagram_width + side_gap
        left_items, right_items = split_placements_for_side_columns(sheet.placements)
        row_height = 24.0
        header_height = 24.0
        list_height = header_height + max(len(left_items), len(right_items)) * row_height
        section_height = max(diagram_height, list_height)
        elements.append(svg_rect(diagram_x, diagram_y, diagram_width, diagram_height, "#f8fafc", "#94a3b8", 2, 6))
        elements.append(
            svg_rect(
                diagram_x + EDGE_MARGIN * scale,
                diagram_y + EDGE_MARGIN * scale,
                (board_length - EDGE_MARGIN * 2.0) * scale,
                (board_width - EDGE_MARGIN * 2.0) * scale,
                "none",
                "#64748b",
                1.5,
                0,
                'stroke-dasharray="8 6"',
            )
        )
        elements.append(svg_text(left_x, diagram_y, "\u7f16\u53f7", size=16, weight=700, fill="#374151"))
        elements.append(svg_text(left_x + 52, diagram_y, "\u539f\u5c3a\u5bf8", size=16, weight=700, fill="#374151"))
        elements.append(svg_text(right_x, diagram_y, "\u7f16\u53f7", size=16, weight=700, fill="#374151"))
        elements.append(svg_text(right_x + 52, diagram_y, "\u539f\u5c3a\u5bf8", size=16, weight=700, fill="#374151"))
        list_y = diagram_y + header_height
        for placement in left_items:
            elements.append(svg_text(left_x, list_y, f"#{placement.part.item_no}", size=15, fill="#111827"))
            elements.append(svg_text(left_x + 52, list_y, f"{fmt_number(placement.part.length)}x{fmt_number(placement.part.width)}", size=15, fill="#111827"))
            list_y += row_height
        list_y = diagram_y + header_height
        for placement in right_items:
            elements.append(svg_text(right_x, list_y, f"#{placement.part.item_no}", size=15, fill="#111827"))
            elements.append(svg_text(right_x + 52, list_y, f"{fmt_number(placement.part.length)}x{fmt_number(placement.part.width)}", size=15, fill="#111827"))
            list_y += row_height
        for index, placement in enumerate(sheet.placements):
            x = diagram_x + placement.x * scale
            rect_y = diagram_y + placement.y * scale
            width = placement.length * scale
            height = placement.width * scale
            fill = palette[(placement.part.item_no - 1) % len(palette)]
            elements.append(svg_rect(x, rect_y, width, height, fill, "#ffffff", 2, 3, 'fill-opacity="0.84"'))
            if width >= 80 and height >= 46:
                label = f"#{placement.part.item_no}"
                elements.append(svg_text(x + width / 2, rect_y + height / 2 - 4, label, size=21, weight=700, fill="#ffffff", anchor="middle"))
            elif width >= 44 and height >= 24:
                elements.append(svg_text(x + width / 2, rect_y + height / 2 + 7, f"#{placement.part.item_no}", size=18, weight=700, fill="#ffffff", anchor="middle"))
        y += section_height + 34

    return y + 28, elements


def write_report_svg(
    path: str,
    results: Sequence[ThicknessResult],
    board_length: float,
    board_width: float,
    image_width: int,
) -> int:
    if image_width < 700:
        raise ValueError("--image-width must be at least 700.")

    card_width = image_width - 48
    content: List[str] = []
    y = 44.0
    total_integer = sum(result.integer_sheets for result in results)
    total_equivalent = ceil_to_tenth(sum(result.sheet_equivalent for result in results))
    total_weight_kg = sum(result.total_weight_kg for result in results)

    content.append(svg_text(34, y, "\u677f\u6750\u6392\u677f\u7ed3\u679c", size=44, weight=800, fill="#0f172a"))
    y += 44
    content.append(
        svg_text(
            36,
            y,
            f"\u6574\u677f {fmt_number(board_length)} x {fmt_number(board_width)} mm\uff0c\u56db\u5468\u7559\u8fb9 {fmt_number(EDGE_MARGIN)} mm\uff0c\u5200\u7f1d {fmt_number(MIN_KERF)}-{fmt_number(MAX_KERF)} mm",
            size=24,
            fill="#475569",
        )
    )
    y += 34
    content.append(svg_text(36, y, f"\u5b9e\u9645\u6574\u5f20\uff1a{total_integer}\u5f20    \u9762\u79ef\u6298\u7b97\uff1a{total_equivalent:.1f}\u5f20", size=26, weight=700, fill="#111827"))
    y += 34
    if total_weight_kg > 0:
        content.append(svg_text(36, y, f"\u603b\u91cd\u91cf\uff1a{total_weight_kg:.1f} kg", size=24, weight=700, fill="#111827"))
        y += 30

    for result in results:
        y += 18
        part_count = sum(len(sheet.placements) for sheet in result.sheets)
        title = f"厚度 {fmt_number(result.thickness)} mm"
        subtitle = f"{part_count}块，实际 {result.integer_sheets} 张整板，面积折算 {result.sheet_equivalent:.1f} 张"
        card_height, card_elements = build_report_card(title, subtitle, result, board_length, board_width, card_width)
        content.append(svg_rect(24, y, card_width, card_height, "#ffffff", "#d1d5db", 1.2, 12))
        content.append(f'<g transform="translate(24,{y:.1f})">')
        content.extend(card_elements)
        content.append("</g>")
        y += card_height + 18

    height = int(math.ceil(y + 34))
    svg = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{image_width}" height="{height}" viewBox="0 0 {image_width} {height}">',
        svg_rect(0, 0, image_width, height, "#f3f4f6", radius=0),
        *content,
        "</svg>",
    ]
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(svg))
    return height


def write_report_png(
    path: str,
    results: Sequence[ThicknessResult],
    board_length: float,
    board_width: float,
    image_width: int,
) -> None:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise RuntimeError("PNG report requires Pillow. Install it with: pip install pillow") from exc

    if image_width < 760:
        raise ValueError("--image-width must be at least 760.")

    font_candidates = [
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
        r"C:\Windows\Fonts\arial.ttf",
    ]
    bold_candidates = [
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\arialbd.ttf",
    ]

    def font(size: int, bold: bool = False):
        for candidate in (bold_candidates if bold else font_candidates):
            if os.path.exists(candidate):
                return ImageFont.truetype(candidate, size)
        return ImageFont.load_default()

    fonts = {
        "title": font(44, True),
        "h2": font(34, True),
        "h3": font(28, True),
        "body": font(22),
        "small": font(17),
        "meta": font(24),
        "bold": font(22, True),
    }

    card_width = image_width - 48
    palette = ["#2563eb", "#059669", "#dc2626", "#7c3aed", "#ea580c", "#0891b2", "#be123c"]

    def text(draw, xy, value, fill="#1f2937", name="body"):
        if draw is not None:
            draw.text(xy, str(value), fill=fill, font=fonts[name])

    def line(draw, xy, fill="#e5e7eb", width=1):
        if draw is not None:
            draw.line(xy, fill=fill, width=width)

    def rounded(draw, xy, fill, outline=None, width=1, radius=8):
        if draw is not None:
            draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)

    def card(draw, x: float, y: float, result: ThicknessResult) -> float:
        local_y = y + 34
        part_count = sum(len(sheet.placements) for sheet in result.sheets)
        card_title = f"\u539a\u5ea6 {fmt_number(result.thickness)} mm"
        subtitle = f"{part_count}\u5757\uff0c\u5b9e\u9645 {result.integer_sheets} \u5f20\u6574\u677f\uff0c\u9762\u79ef\u6298\u7b97 {result.sheet_equivalent:.1f} \u5f20"

        text(draw, (x + 28, local_y), card_title, "#111827", "h2")
        local_y += 44
        text(draw, (x + 28, local_y), subtitle, "#6b7280", "meta")
        local_y += 54
        if result.total_weight_kg > 0:
            text(draw, (x + 28, local_y), f"\u603b\u91cd\u91cf {result.total_weight_kg:.1f} kg", "#111827", "meta")
            local_y += 38

        text(draw, (x + 28, local_y), "\u96f6\u5207\u6e05\u5355\u6c47\u603b", "#111827", "h3")
        local_y += 42
        text(draw, (x + 28, local_y), "\u5c3a\u5bf8(mm)", "#374151", "bold")
        text(draw, (x + 280, local_y), "\u6570\u91cf", "#374151", "bold")
        text(draw, (x + 430, local_y), "\u9762\u79ef(mm\u00b2)", "#374151", "bold")
        local_y += 34
        line(draw, (x + 24, local_y, x + card_width - 24, local_y), "#e5e7eb")
        local_y += 18

        placements = [placement for sheet in result.sheets for placement in sheet.placements]
        for length, width_value, quantity, area in aggregate_parts(placements):
            text(draw, (x + 28, local_y), f"{fmt_number(length)} x {fmt_number(width_value)}")
            text(draw, (x + 280, local_y), quantity)
            text(draw, (x + 430, local_y), f"{area:.1f}")
            local_y += 36

        side_panel_width = 150.0
        side_gap = 14.0
        max_diagram_width = card_width - 56 - side_panel_width * 2.0 - side_gap * 2.0
        max_diagram_height = 680.0
        for sheet in result.sheets:
            local_y += 22
            fraction = ceil_to_tenth(sheet.used_area / (board_length * board_width))
            text(draw, (x + 28, local_y), f"\u6574\u677f {sheet.sheet_no}\uff1a{len(sheet.placements)}\u5757\uff0c\u9762\u79ef\u6298\u7b97 {fraction:.1f}\u5f20", "#111827", "h3")
            local_y += 48

            diagram_width, diagram_height, scale = compute_diagram_geometry(
                board_length,
                board_width,
                max_diagram_width,
                max_diagram_height,
            )
            diagram_x = x + 28 + side_panel_width + side_gap + (max_diagram_width - diagram_width) / 2.0
            diagram_y = local_y
            left_x = x + 28
            right_x = diagram_x + diagram_width + side_gap
            left_items, right_items = split_placements_for_side_columns(sheet.placements)
            row_height = 24.0
            header_height = 24.0
            list_height = header_height + max(len(left_items), len(right_items)) * row_height
            section_height = max(diagram_height, list_height)
            rounded(draw, (diagram_x, diagram_y, diagram_x + diagram_width, diagram_y + diagram_height), "#f8fafc", "#94a3b8", 2, 6)
            usable = (
                diagram_x + EDGE_MARGIN * scale,
                diagram_y + EDGE_MARGIN * scale,
                diagram_x + (board_length - EDGE_MARGIN) * scale,
                diagram_y + (board_width - EDGE_MARGIN) * scale,
            )
            if draw is not None:
                draw.rectangle(usable, outline="#64748b", width=1)
            text(draw, (left_x, diagram_y), "\u7f16\u53f7", "#374151", "small")
            text(draw, (left_x + 52, diagram_y), "\u539f\u5c3a\u5bf8", "#374151", "small")
            text(draw, (right_x, diagram_y), "\u7f16\u53f7", "#374151", "small")
            text(draw, (right_x + 52, diagram_y), "\u539f\u5c3a\u5bf8", "#374151", "small")
            list_y = diagram_y + header_height
            for placement in left_items:
                text(draw, (left_x, list_y), f"#{placement.part.item_no}", "#111827", "small")
                text(draw, (left_x + 52, list_y), f"{fmt_number(placement.part.length)}x{fmt_number(placement.part.width)}", "#111827", "small")
                list_y += row_height
            list_y = diagram_y + header_height
            for placement in right_items:
                text(draw, (right_x, list_y), f"#{placement.part.item_no}", "#111827", "small")
                text(draw, (right_x + 52, list_y), f"{fmt_number(placement.part.length)}x{fmt_number(placement.part.width)}", "#111827", "small")
                list_y += row_height
            for placement in sheet.placements:
                px = diagram_x + placement.x * scale
                py = diagram_y + placement.y * scale
                pw = placement.length * scale
                ph = placement.width * scale
                color = palette[(placement.part.item_no - 1) % len(palette)]
                rounded(draw, (px, py, px + pw, py + ph), color, "#ffffff", 2, 3)
                if draw is not None and pw >= 80 and ph >= 48:
                    label = f"#{placement.part.item_no}"
                    box = draw.textbbox((0, 0), label, font=fonts["bold"])
                    draw.text((px + pw / 2 - (box[2] - box[0]) / 2, py + ph / 2 - 12), label, fill="#ffffff", font=fonts["bold"])
                elif draw is not None and pw >= 44 and ph >= 24:
                    label = f"#{placement.part.item_no}"
                    box = draw.textbbox((0, 0), label, font=fonts["small"])
                    draw.text((px + pw / 2 - (box[2] - box[0]) / 2, py + ph / 2 - 12), label, fill="#ffffff", font=fonts["small"])
            local_y += section_height + 34

        return local_y - y + 26

    def render(draw=None) -> int:
        y = 44.0
        total_integer = sum(result.integer_sheets for result in results)
        total_equivalent = ceil_to_tenth(sum(result.sheet_equivalent for result in results))
        total_weight_kg = sum(result.total_weight_kg for result in results)
        text(draw, (34, y), "\u677f\u6750\u6392\u677f\u7ed3\u679c", "#0f172a", "title")
        y += 58
        text(draw, (36, y), f"\u6574\u677f {fmt_number(board_length)} x {fmt_number(board_width)} mm\uff0c\u56db\u5468\u7559\u8fb9 {fmt_number(EDGE_MARGIN)} mm\uff0c\u5200\u7f1d {fmt_number(MIN_KERF)}-{fmt_number(MAX_KERF)} mm", "#475569", "meta")
        y += 38
        text(draw, (36, y), f"\u5b9e\u9645\u6574\u5f20\uff1a{total_integer}\u5f20    \u9762\u79ef\u6298\u7b97\uff1a{total_equivalent:.1f}\u5f20", "#111827", "h3")
        y += 54
        if total_weight_kg > 0:
            text(draw, (36, y), f"\u603b\u91cd\u91cf\uff1a{total_weight_kg:.1f} kg", "#111827", "meta")
            y += 40
        text(draw, (36, y), "\u677f\u6750\u603b\u660e\u7ec6", "#111827", "h3")
        y += 34
        text(draw, (36, y), "\u539a\u5ea6", "#374151", "bold")
        text(draw, (220, y), "\u7528\u91cf(\u5f20)", "#374151", "bold")
        text(draw, (430, y), "\u91cd\u91cf(kg)", "#374151", "bold")
        y += 28
        line(draw, (32, y, image_width - 32, y), "#e5e7eb")
        y += 18
        for result in results:
            text(draw, (36, y), f"{fmt_number(result.thickness)} mm", "#111827", "body")
            text(draw, (220, y), f"{result.sheet_equivalent:.1f}", "#111827", "body")
            text(draw, (430, y), f"{result.total_weight_kg:.1f}" if result.total_weight_kg > 0 else "-", "#111827", "body")
            y += 32
        y += 14

        for result in results:
            y += 18
            card_height = card(None, 24, y, result)
            rounded(draw, (24, y, 24 + card_width, y + card_height), "#ffffff", "#d1d5db", 1, 12)
            card(draw, 24, y, result)
            y += card_height + 18
        return int(math.ceil(y + 34))

    height = render(None)
    image = Image.new("RGB", (image_width, height), "#f3f4f6")
    draw = ImageDraw.Draw(image)
    render(draw)
    image.save(path)


def print_summary(results: Sequence[ThicknessResult], board_length: float, board_width: float) -> None:
    print("Board cutting summary")
    print(f"Full board: {fmt_number(board_length)} x {fmt_number(board_width)} mm")
    print(f"Margin: {fmt_number(EDGE_MARGIN)} mm each side")
    print(f"Kerf range: {fmt_number(MIN_KERF)}-{fmt_number(MAX_KERF)} mm")
    print()

    total_equivalent = 0.0
    total_integer_sheets = 0
    for result in results:
        total_equivalent += result.sheet_equivalent
        total_integer_sheets += result.integer_sheets
        part_count = sum(len(sheet.placements) for sheet in result.sheets)
        print(
            f"Thickness {fmt_number(result.thickness)} mm: "
            f"{part_count} parts, {result.integer_sheets} full sheets, "
            f"{result.sheet_equivalent:.1f} sheets by area"
        )
        if result.total_weight_kg > 0:
            print(f"  Total weight: {result.total_weight_kg:.1f} kg")
        for sheet in result.sheets:
            fraction = ceil_to_tenth(sheet.used_area / (board_length * board_width))
            print(
                f"  Sheet {sheet.sheet_no}: {len(sheet.placements)} parts, "
                f"used area {sheet.used_area:.1f} mm^2, area fraction {fraction:.1f}"
            )
    print()
    print(f"Total full sheets: {total_integer_sheets}")
    print(f"Total sheet equivalent: {ceil_to_tenth(total_equivalent):.1f}")
    total_weight_kg = sum(result.total_weight_kg for result in results)
    if total_weight_kg > 0:
        print(f"Total weight: {total_weight_kg:.1f} kg")


def build_layout_results(
    input_path: str,
    board_length: float,
    board_width: float,
    weight_table_path: Optional[str],
) -> Tuple[List[ThicknessResult], Optional[str]]:
    parts = read_parts(input_path)
    weight_table = load_weight_table(weight_table_path) if weight_table_path else None
    grouped = group_by_thickness(parts)
    results = [
        pack_parts_for_thickness(thickness, group, board_length, board_width, weight_table)
        for thickness, group in grouped.items()
    ]
    return results, weight_table_path


def generate_layout_outputs(
    input_path: str,
    board_length: float,
    board_width: float,
    *,
    weight_table_path: Optional[str],
    output_csv_path: Optional[str] = None,
    report_svg_path: Optional[str] = None,
    image_output_path: Optional[str] = None,
    image_width: int = 960,
) -> Tuple[List[ThicknessResult], str]:
    results, effective_weight_table_path = build_layout_results(
        input_path,
        board_length,
        board_width,
        weight_table_path,
    )
    if output_csv_path:
        write_output_csv(output_csv_path, results)
    if report_svg_path:
        write_report_svg(report_svg_path, results, board_length, board_width, image_width)
    final_image_output_path = image_output_path or default_report_png_path(input_path)
    write_report_png(final_image_output_path, results, board_length, board_width, image_width)
    return results, effective_weight_table_path or ""


def run(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if args.board_length <= EDGE_MARGIN * 2.0 or args.board_width <= EDGE_MARGIN * 2.0:
        raise ValueError("Board length and width must be greater than 20mm.")

    weight_table_path = discover_weight_table_path()
    results, effective_weight_table_path = generate_layout_outputs(
        args.input,
        args.board_length,
        args.board_width,
        weight_table_path=weight_table_path,
        output_csv_path=args.output,
        report_svg_path=args.report_svg,
        image_output_path=args.image_output,
        image_width=args.image_width,
    )

    print_summary(results, args.board_length, args.board_width)
    if effective_weight_table_path:
        print(f"Weight table: {effective_weight_table_path}")
    if args.output:
        print(f"Detailed placements written to: {args.output}")
    if args.report_svg:
        print(f"SVG report written to: {args.report_svg}")
    image_output_path = args.image_output or default_report_png_path(args.input)
    print(f"PNG report written to: {image_output_path}")
    return 0


def main() -> int:
    try:
        return run()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
