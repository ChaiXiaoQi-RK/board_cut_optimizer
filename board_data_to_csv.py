#!/usr/bin/env python3
"""
Convert pasted board data into CSV for board_cut_optimizer.py.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import re
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple


DEFAULT_ARCHIVE_ROOT = "D:\\Works\\\u7535\u5546\\\u6d77\u6d0b\u677f\\\u6d77\u6d0b\u677f\u8ba2\u5355\u7559\u6863"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

CHAR_THICK = "\u539a"
CHAR_QTY = "\u6570\u91cf"
CHAR_PIECES = ("\u5757", "\u4ef6", "\u7247", "\u4e2a")
CHAR_ENUM_SEPARATORS = ("\u3001",)


@dataclass(frozen=True)
class NumberToken:
    value: float
    start: int
    end: int


@dataclass(frozen=True)
class BoardRow:
    length: float
    width: float
    thickness: float
    quantity: int
    source_line: int


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
    }
    return aliases.get(text, text)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert pasted board data to board_cut_optimizer CSV."
    )
    parser.add_argument("--filename", required=True, help="Output CSV filename. .csv is added if omitted.")
    parser.add_argument("--input", help="Text file containing pasted board data.")
    parser.add_argument("--data", help="Board data text. If omitted, data is read from stdin.")
    parser.add_argument("--customer", help="Optional customer/order folder below the date folder.")
    parser.add_argument("--archive-root", default=DEFAULT_ARCHIVE_ROOT, help="Archive root folder.")
    parser.add_argument("--date", help="Optional archive date, such as 2026-6-24. Defaults to today.")
    parser.add_argument("--default-thickness", type=float, help="Thickness used when a line only has length and width.")
    parser.add_argument("--no-combine", action="store_true", help="Do not combine identical rows.")
    return parser.parse_args(argv)


def normalize_filename(filename: str) -> str:
    name = filename.strip().strip('"')
    if not name:
        raise ValueError("--filename cannot be empty.")
    if not name.lower().endswith(".csv"):
        name += ".csv"
    if any(char in name for char in '<>:"/\\|?*'):
        raise ValueError(f"Filename contains invalid Windows characters: {name}")
    return name


def filename_stem(filename: str) -> str:
    stem, _ = os.path.splitext(filename)
    return stem


def parse_archive_date(value: Optional[str]) -> dt.date:
    if not value:
        return dt.date.today()
    text = value.strip()
    match = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", text)
    if not match:
        raise ValueError("--date must look like 2026-6-24.")
    return dt.date(int(match.group(1)), int(match.group(2)), int(match.group(3)))


def build_output_path(
    archive_root: str,
    date_value: dt.date,
    filename: str,
    customer: Optional[str],
) -> str:
    month_dir = f"{date_value.year}-{date_value.month}"
    day_dir = f"{date_value.month}-{date_value.day}"
    output_dir = os.path.join(archive_root, month_dir, day_dir)
    folder_name = customer.strip().strip('"') if customer else filename_stem(filename)
    if any(char in folder_name for char in '<>:"/\\|?*'):
        raise ValueError(f"Folder name contains invalid Windows characters: {folder_name}")
    output_dir = os.path.join(output_dir, folder_name)
    return os.path.join(output_dir, filename)


def read_raw_data(args: argparse.Namespace) -> str:
    if args.data is not None:
        return args.data
    if args.input:
        with open(args.input, "r", encoding="utf-8-sig") as handle:
            return handle.read()
    if sys.stdin.isatty():
        raise ValueError("No input data. Pass --data, --input, or pipe text into stdin.")
    return sys.stdin.read()


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


def validate_thicknesses(rows: Sequence[BoardRow], weight_table: Optional[Dict[float, float]]) -> None:
    if not weight_table:
        return
    allowed = list(weight_table.keys())
    for row in rows:
        if not any(abs(row.thickness - allowed_value) < 1e-9 for allowed_value in allowed):
            allowed_text = ", ".join(str(int(value)) if abs(value - int(value)) < 1e-9 else f"{value:.1f}" for value in sorted(allowed))
            raise ValueError(
                f"Line {row.source_line}: thickness {row.thickness} is not in weight table. "
                f"Allowed thicknesses: {allowed_text}"
            )


def normalize_line(line: str) -> str:
    normalized = line.strip()
    for marker in CHAR_ENUM_SEPARATORS:
        normalized = normalized.replace(marker, " ")
    normalized = normalized.replace("\u00d7", "x").replace("*", "x").replace("X", "x")
    normalized = normalized.replace("\uff0c", ",").replace("\uff1b", ";").replace("\uff1a", ":")
    normalized = re.sub(r"^\s*\d+\s*[\.\)\]]\s*", "", normalized)
    return normalized


def find_numbers(line: str) -> List[NumberToken]:
    return [
        NumberToken(float(match.group()), match.start(), match.end())
        for match in re.finditer(r"\d+(?:\.\d+)?", line)
    ]


def find_quantity(line: str) -> Optional[NumberToken]:
    patterns = [
        rf"(?:{CHAR_QTY}|qty|q)\s*[:=]?\s*(\d+(?:\.\d+)?)",
        rf"(\d+(?:\.\d+)?)\s*(?:{'|'.join(CHAR_PIECES)}|pcs|pc)(?=\s|$)",
    ]
    matches: List[NumberToken] = []
    for pattern in patterns:
        for match in re.finditer(pattern, line, flags=re.IGNORECASE):
            start, end = match.span(1)
            matches.append(NumberToken(float(match.group(1)), start, end))
    if matches:
        return sorted(matches, key=lambda token: token.start)[-1]
    return None


def find_thickness(line: str) -> Optional[NumberToken]:
    patterns = [
        rf"(?:{CHAR_THICK}|{CHAR_THICK}\u5ea6)\s*[:=]?\s*(\d+(?:\.\d+)?)",
        r"\bt\s*[:=]?\s*(\d+(?:\.\d+)?)",
        rf"(\d+(?:\.\d+)?)\s*(?:mm)?\s*{CHAR_THICK}(?!\s*\d)",
    ]
    for pattern in patterns:
        matches = []
        for match in re.finditer(pattern, line, flags=re.IGNORECASE):
            start, end = match.span(1)
            matches.append(NumberToken(float(match.group(1)), start, end))
        if matches:
            return sorted(matches, key=lambda token: token.start)[0]
    return None


def same_span(left: NumberToken, right: NumberToken) -> bool:
    return left.start == right.start and left.end == right.end


def as_quantity(value: float, line_no: int) -> int:
    quantity = int(value)
    if quantity <= 0 or abs(quantity - value) > 1e-9:
        raise ValueError(f"Line {line_no}: quantity must be a positive integer, got {value}.")
    return quantity


def parse_by_x_groups(line: str, line_no: int) -> Optional[BoardRow]:
    compact = re.sub(r"\s+", "", line.lower())
    compact = compact.replace("\u00d7", "x").replace("*", "x")

    piece_units = f"(?:{'|'.join(CHAR_PIECES)}|pcs|pc)"

    def split_thickness_and_quantity(suffix_digits: str) -> Optional[Tuple[float, int]]:
        for split_at in range(len(suffix_digits) - 1, 0, -1):
            thickness_text = suffix_digits[:split_at]
            quantity_text = suffix_digits[split_at:]
            thickness_value = float(thickness_text)
            quantity_value = float(quantity_text)
            if thickness_value <= 0 or thickness_value >= 100:
                continue
            try:
                quantity_int = as_quantity(quantity_value, line_no)
            except ValueError:
                continue
            return thickness_value, quantity_int
        return None

    match = re.match(r"^(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)mm(\d+)?$", compact)
    if match:
        return BoardRow(
            float(match.group(1)),
            float(match.group(2)),
            float(match.group(3)),
            as_quantity(float(match.group(4)), line_no) if match.group(4) else 1,
            line_no,
        )

    match = re.match(r"^(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)(?:mm)?(\d+)\D+$", compact)
    if match:
        return BoardRow(
            float(match.group(1)),
            float(match.group(2)),
            float(match.group(3)),
            as_quantity(float(match.group(4)), line_no),
            line_no,
        )

    match = re.match(rf"^(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)(?:mm)?(\d+){piece_units}$", compact)
    if match:
        return BoardRow(
            float(match.group(1)),
            float(match.group(2)),
            float(match.group(3)),
            as_quantity(float(match.group(4)), line_no),
            line_no,
        )

    match = re.match(rf"^(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)(?:mm)?$", compact)
    if match:
        return BoardRow(float(match.group(1)), float(match.group(2)), float(match.group(3)), 1, line_no)

    match = re.match(rf"^(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)$", compact)
    if match:
        return BoardRow(float(match.group(1)), float(match.group(2)), float(match.group(3)), 1, line_no)

    match = re.match(rf"^(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)x(\d+){piece_units}$", compact)
    if match:
        split = split_thickness_and_quantity(match.group(3))
        if split is not None:
            thickness_value, quantity_int = split
            return BoardRow(float(match.group(1)), float(match.group(2)), thickness_value, quantity_int, line_no)

    match = re.match(rf"^(?:{CHAR_THICK}|{CHAR_THICK}\u5ea6)(\d+(?:\.\d+)?)(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)$", compact)
    if match:
        return BoardRow(float(match.group(2)), float(match.group(3)), float(match.group(1)), 1, line_no)

    match = re.match(rf"^(?:{CHAR_THICK}|{CHAR_THICK}\u5ea6)(\d+)(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?){piece_units}$", compact)
    if match:
        split = split_thickness_and_quantity(match.group(1))
        if split is not None:
            thickness_value, quantity_int = split
            return BoardRow(float(match.group(2)), float(match.group(3)), thickness_value, quantity_int, line_no)

    match = re.match(rf"^(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)(?:{CHAR_THICK}|{CHAR_THICK}\u5ea6)(\d+(?:\.\d+)?)(?:{CHAR_QTY})?(\d+(?:\.\d+)?)?(?:{piece_units})?$", compact)
    if match:
        quantity = match.group(4)
        return BoardRow(
            float(match.group(1)),
            float(match.group(2)),
            float(match.group(3)),
            as_quantity(float(quantity), line_no) if quantity else 1,
            line_no,
        )

    qty_match = re.search(r"(\d+(?:\.\d+)?)(?:pcs|pc)\b", compact)
    if not qty_match:
        qty_match = re.search(rf"(\d+(?:\.\d+)?)(?:{'|'.join(CHAR_PIECES)})", compact)
    if not qty_match:
        qty_match = re.search(rf"{CHAR_QTY}[:=]?(\d+(?:\.\d+)?)", compact)
    quantity = as_quantity(float(qty_match.group(1)), line_no) if qty_match else 1
    compact_without_qty = compact
    if qty_match:
        compact_without_qty = compact[: qty_match.start()] + compact[qty_match.end() :]

    leading = re.match(rf"(?:{CHAR_THICK}|{CHAR_THICK}\u5ea6)(\d+(?:\.\d+)?)(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)", compact_without_qty)
    if leading:
        thickness = float(leading.group(1))
        length = float(leading.group(2))
        width = float(leading.group(3))
        return BoardRow(length, width, thickness, quantity, line_no)

    middle = re.match(rf"(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)(?:{CHAR_THICK}|{CHAR_THICK}\u5ea6)(\d+(?:\.\d+)?)", compact_without_qty)
    if middle:
        length = float(middle.group(1))
        width = float(middle.group(2))
        thickness = float(middle.group(3))
        return BoardRow(length, width, thickness, quantity, line_no)

    trailing = re.match(rf"(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)(\d+(?:\.\d+)?)", compact_without_qty)
    if trailing and compact_without_qty.count("x") == 2:
        length = float(trailing.group(1))
        width = float(trailing.group(2))
        thickness = float(trailing.group(3))
        return BoardRow(length, width, thickness, quantity, line_no)

    return None


def parse_numeric_fallback(numbers: Sequence[NumberToken], line_no: int) -> Optional[BoardRow]:
    values = [token.value for token in numbers]
    if len(values) == 4:
        a, b, c, d = values
        if a < 100 and b >= 100 and c >= 100 and abs(d - int(d)) < 1e-9:
            return BoardRow(b, c, a, as_quantity(d, line_no), line_no)
        if c < 100 and a >= 100 and b >= 100 and abs(d - int(d)) < 1e-9:
            return BoardRow(a, b, c, as_quantity(d, line_no), line_no)
    return None


def parse_board_line(line: str, line_no: int, default_thickness: Optional[float]) -> Optional[BoardRow]:
    normalized = normalize_line(line)
    if not normalized or normalized.startswith("#"):
        return None

    x_parsed = parse_by_x_groups(normalized, line_no)
    if x_parsed is not None:
        return x_parsed

    numbers = find_numbers(normalized)
    if not numbers:
        return None

    numeric_fallback = parse_numeric_fallback(numbers, line_no)
    if numeric_fallback is not None:
        return numeric_fallback

    quantity_token = find_quantity(normalized)
    thickness_token = find_thickness(normalized)
    remaining = [
        token
        for token in numbers
        if not (quantity_token and same_span(token, quantity_token))
        and not (thickness_token and same_span(token, thickness_token))
    ]

    quantity = as_quantity(quantity_token.value, line_no) if quantity_token else None
    thickness = thickness_token.value if thickness_token else None

    if thickness is not None:
        if len(remaining) < 2:
            raise ValueError(f"Line {line_no}: cannot find length and width in: {line}")
        if thickness_token and thickness_token.start <= remaining[0].start and len(remaining) >= 2:
            length = remaining[-2].value
            width = remaining[-1].value
        else:
            length = remaining[0].value
            width = remaining[1].value
        if quantity is None:
            quantity = 1
    else:
        if quantity is not None:
            if len(remaining) >= 3:
                length, width, thickness = remaining[0].value, remaining[1].value, remaining[2].value
            elif len(remaining) >= 2 and default_thickness is not None:
                length, width, thickness = remaining[0].value, remaining[1].value, default_thickness
            else:
                raise ValueError(f"Line {line_no}: cannot find length, width and thickness in: {line}")
        elif len(remaining) >= 4:
            length, width, thickness = remaining[0].value, remaining[1].value, remaining[2].value
            quantity = as_quantity(remaining[3].value, line_no)
        elif len(remaining) == 3:
            length, width, thickness = remaining[0].value, remaining[1].value, remaining[2].value
            quantity = 1
        elif len(remaining) == 2 and default_thickness is not None:
            length, width, thickness = remaining[0].value, remaining[1].value, default_thickness
            quantity = 1
        else:
            raise ValueError(f"Line {line_no}: cannot parse board size in: {line}")

    if length <= 0 or width <= 0 or thickness <= 0:
        raise ValueError(f"Line {line_no}: length, width and thickness must be greater than 0.")
    return BoardRow(length, width, thickness, quantity, line_no)


def parse_board_data(raw: str, default_thickness: Optional[float]) -> List[BoardRow]:
    rows = []
    for line_no, line in enumerate(raw.splitlines(), start=1):
        parsed = parse_board_line(line, line_no, default_thickness)
        if parsed is not None:
            rows.append(parsed)
    if not rows:
        raise ValueError("No usable board rows were found.")
    return rows


def combine_rows(rows: Sequence[BoardRow]) -> List[BoardRow]:
    grouped: Dict[Tuple[float, float, float], Tuple[int, int]] = {}
    for row in rows:
        key = (row.length, row.width, row.thickness)
        quantity, first_line = grouped.get(key, (0, row.source_line))
        grouped[key] = (quantity + row.quantity, first_line)
    combined = [
        BoardRow(length, width, thickness, quantity, first_line)
        for (length, width, thickness), (quantity, first_line) in grouped.items()
    ]
    return sorted(combined, key=lambda row: (row.thickness, row.length, row.width))


def fmt_number(value: float) -> str:
    if abs(value - int(value)) < 1e-9:
        return str(int(value))
    return f"{value:.1f}"


def write_csv(path: str, rows: Sequence[BoardRow]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=["length", "width", "thickness", "quantity"])
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "length": fmt_number(row.length),
                    "width": fmt_number(row.width),
                    "thickness": fmt_number(row.thickness),
                    "quantity": row.quantity,
                }
            )


def convert_board_data_to_csv(
    raw_text: str,
    filename: str,
    archive_root: str,
    weight_table_path: Optional[str],
    *,
    customer: Optional[str] = None,
    archive_date: Optional[dt.date] = None,
    default_thickness: Optional[float] = None,
    combine: bool = True,
) -> Tuple[str, List[BoardRow], Optional[str]]:
    rows = parse_board_data(raw_text, default_thickness)
    weight_table = load_weight_table(weight_table_path) if weight_table_path else None
    validate_thicknesses(rows, weight_table)
    output_rows = combine_rows(rows) if combine else list(rows)

    normalized_filename = normalize_filename(filename)
    effective_date = archive_date or dt.date.today()
    output_path = build_output_path(archive_root, effective_date, normalized_filename, customer)
    write_csv(output_path, output_rows)
    return output_path, output_rows, weight_table_path


def run(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    raw = read_raw_data(args)
    weight_table_path = discover_weight_table_path()
    archive_date = parse_archive_date(args.date)
    output_path, output_rows, _ = convert_board_data_to_csv(
        raw,
        args.filename,
        args.archive_root,
        weight_table_path,
        customer=args.customer,
        archive_date=archive_date,
        default_thickness=args.default_thickness,
        combine=not args.no_combine,
    )

    print(f"CSV written to: {output_path}")
    if weight_table_path:
        print(f"Weight table: {weight_table_path}")
    print(f"Rows: {len(output_rows)}")
    for row in output_rows:
        print(
            f"  {fmt_number(row.length)} x {fmt_number(row.width)} x "
            f"{fmt_number(row.thickness)} mm, quantity {row.quantity}"
        )
    return 0


def main() -> int:
    try:
        return run()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
