#!/usr/bin/env python3
"""在 SVG 或 HTML 中绘制简单折线图、散点图和柱状图。

脚本只依赖 Python 标准库，可直接读取 CSV/TSV 文件。

示例:
    python simple_plot.py --demo
    python simple_plot.py --demo -o demo.svg
    python simple_plot.py data.csv plot.svg --x 日期 --y 销售额 成本 --type line
    python simple_plot.py data.csv chart.html --x 品类 --y 数量 --type bar
"""

from __future__ import annotations

import argparse
import csv
import html
import math
import re
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


SERIES_COLORS = (
    "#2563EB",
    "#EA580C",
    "#16A34A",
    "#9333EA",
    "#0891B2",
    "#DC2626",
    "#CA8A04",
    "#4B5563",
)
SERIES_MARKERS = ("circle", "square", "diamond", "triangle", "cross", "star")


@dataclass(frozen=True)
class Series:
    name: str
    values: tuple[float | None, ...]


@dataclass(frozen=True)
class ChartData:
    x_labels: tuple[str, ...]
    x_values: tuple[float | None, ...]
    x_is_numeric: bool
    series: tuple[Series, ...]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="从 CSV/TSV 数据生成简单折线图、散点图或柱状图。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python simple_plot.py --demo\n"
            "  python simple_plot.py --demo -o demo.svg\n"
            "  python simple_plot.py data.csv plot.svg --x 日期 --y 销售额 成本\n"
            "  python simple_plot.py data.csv plot.html --x 品类 --y 数量 --type bar"
        ),
    )
    parser.add_argument(
        "input",
        nargs="?",
        help="CSV/TSV 文件路径；省略时必须使用 --demo。",
    )
    parser.add_argument(
        "output",
        nargs="?",
        default="plot.svg",
        help="输出文件路径，扩展名需为 .svg 或 .html（默认: plot.svg）。",
    )
    parser.add_argument(
        "-o",
        "--output-file",
        dest="output_file",
        help="显式指定输出文件；使用 --demo 时推荐通过此参数指定路径。",
    )
    parser.add_argument("--x", help="横轴列名；省略时使用第一列。")
    parser.add_argument(
        "--y",
        nargs="+",
        help="一个或多个数值列名；省略时自动选择所有数值列。",
    )
    parser.add_argument(
        "--type",
        choices=("line", "bar", "scatter"),
        default="line",
        help="图形类型（默认: line）。",
    )
    parser.add_argument("--title", default="数据图", help="图表标题。")
    parser.add_argument("--xlabel", help="横轴标签；省略时使用列名。")
    parser.add_argument("--ylabel", help="纵轴标签；单系列时省略则使用列名。")
    parser.add_argument("--width", type=int, default=1200, help="图像宽度（默认: 1200）。")
    parser.add_argument("--height", type=int, default=720, help="图像高度（默认: 720）。")
    parser.add_argument("--encoding", default="utf-8-sig", help="文件编码（默认: utf-8-sig）。")
    parser.add_argument("--delimiter", help="分隔符；省略时自动识别，常用值为 , 或 \\t。")
    parser.add_argument(
        "--sort-x",
        action="store_true",
        help="按横轴升序排列数据；数值横轴无效时保持原顺序。",
    )
    parser.add_argument("--demo", action="store_true", help="生成内置示例图，无需输入文件。")
    parser.add_argument(
        "--list-columns",
        action="store_true",
        help="只列出数据列名及可识别为数值的列，不绘图。",
    )
    return parser.parse_args()


def read_table(
    path: Path, encoding: str, delimiter: str | None
) -> tuple[list[str], list[dict[str, str]]]:
    encodings = [encoding]
    for candidate in ("utf-8-sig", "utf-8", "gb18030"):
        if candidate not in encodings:
            encodings.append(candidate)

    last_error: Exception | None = None
    text = ""
    for candidate in encodings:
        try:
            text = path.read_text(encoding=candidate)
            break
        except UnicodeDecodeError as exc:
            last_error = exc
    else:
        raise ValueError(f"无法识别文件编码: {path}") from last_error

    if not text.strip():
        raise ValueError("输入文件为空。")

    if delimiter is None:
        if path.suffix.lower() == ".tsv":
            delimiter = "\t"
        else:
            sample = text[:8192]
            try:
                delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
            except csv.Error:
                delimiter = ","

    reader = csv.DictReader(text.splitlines(), delimiter=delimiter)
    fieldnames = reader.fieldnames or []
    if not fieldnames:
        raise ValueError("未找到表头。")
    if any(name is None or not name.strip() for name in fieldnames):
        raise ValueError("表头包含空列名，请先修正数据文件。")

    rows = list(reader)
    if not rows:
        raise ValueError("数据文件只有表头，没有数据行。")
    return fieldnames, rows


def to_number(value: str | None) -> float | None:
    if value is None:
        return None
    text = value.strip()
    if not text or text.lower() in {"na", "n/a", "null", "none", "nan", "-"}:
        return None
    text = text.replace(",", "").replace("，", "")
    if text.endswith("%"):
        text = text[:-1].strip()
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def is_numeric_column(rows: Sequence[dict[str, str]], column: str) -> bool:
    nonempty = [row.get(column, "").strip() for row in rows if row.get(column, "").strip()]
    if not nonempty:
        return False
    parsed = sum(to_number(value) is not None for value in nonempty)
    return parsed / len(nonempty) >= 0.8


def sort_rows(rows: list[dict[str, str]], x_column: str | None) -> list[dict[str, str]]:
    if not x_column:
        return rows
    numeric = [(to_number(row.get(x_column)), index, row) for index, row in enumerate(rows)]
    if sum(value is not None for value, _, _ in numeric) >= len(rows) * 0.8:
        return [
            row
            for _, _, row in sorted(
                numeric,
                key=lambda item: (
                    item[0] is None,
                    item[0] if item[0] is not None else item[1],
                ),
            )
        ]
    return sorted(rows, key=lambda row: row.get(x_column, ""))


def build_chart_data(
    columns: Sequence[str],
    rows: Sequence[dict[str, str]],
    x_column: str | None,
    y_columns: Sequence[str] | None,
) -> ChartData:
    if x_column is None:
        x_column = columns[0]
    if x_column not in columns:
        raise ValueError(f"横轴列不存在: {x_column}")

    if y_columns:
        missing = [column for column in y_columns if column not in columns]
        if missing:
            raise ValueError(f"纵轴列不存在: {', '.join(missing)}")
        selected_y = list(dict.fromkeys(y_columns))
    else:
        selected_y = [
            column for column in columns if column != x_column and is_numeric_column(rows, column)
        ]
    if not selected_y:
        raise ValueError("没有可绘制的数值列，请用 --y 明确指定列名。")
    if x_column in selected_y:
        raise ValueError("横轴列不能同时作为纵轴数值列。")

    x_labels = tuple(row.get(x_column, "").strip() or f"第{i + 1}行" for i, row in enumerate(rows))
    x_numbers = tuple(to_number(label) for label in x_labels)
    x_is_numeric = sum(value is not None for value in x_numbers) >= len(rows) * 0.8
    if not x_is_numeric:
        x_numbers = tuple(float(index) for index in range(len(rows)))

    series = tuple(
        Series(column, tuple(to_number(row.get(column)) for row in rows)) for column in selected_y
    )
    if all(value is None for item in series for value in item.values):
        raise ValueError("所选纵轴列没有可识别的数值。")
    return ChartData(x_labels, x_numbers, x_is_numeric, series)


def nice_number(value: float, round_down: bool) -> float:
    if value <= 0 or not math.isfinite(value):
        return 1.0
    exponent = math.floor(math.log10(value))
    fraction = value / (10**exponent)
    if round_down:
        nice_fraction = 1 if fraction < 1.5 else 2 if fraction < 2.5 else 2.5 if fraction < 3.5 else 5 if fraction < 7.5 else 10
    else:
        nice_fraction = 1 if fraction <= 1 else 2 if fraction <= 2 else 2.5 if fraction <= 2.5 else 5 if fraction <= 5 else 10
    return nice_fraction * (10**exponent)


def nice_ticks(minimum: float, maximum: float, target_count: int = 6) -> list[float]:
    if not math.isfinite(minimum) or not math.isfinite(maximum):
        return [0.0, 1.0]
    if maximum - minimum < 1e-12:
        delta = abs(maximum) * 0.1 or 1.0
        minimum -= delta
        maximum += delta
    raw_step = (maximum - minimum) / max(target_count - 1, 1)
    step = nice_number(raw_step, False)
    start = math.floor(minimum / step) * step
    end = math.ceil(maximum / step) * step
    count = int(round((end - start) / step)) + 1
    if count > 12:
        step *= 2
        start = math.floor(minimum / step) * step
        end = math.ceil(maximum / step) * step
    return [start + step * index for index in range(int(round((end - start) / step)) + 1)]


def format_number(value: float) -> str:
    if abs(value) < 1e-12:
        value = 0.0
    if abs(value) >= 1_000_000 or (0 < abs(value) < 1e-3):
        return f"{value:.2e}"
    rounded = round(value)
    if abs(value - rounded) < 1e-9:
        return f"{rounded:,}"
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def estimate_text_width(text: str, font_size: float) -> float:
    width = 0.0
    for char in text:
        width += font_size * (0.58 if ord(char) < 128 else 0.95)
    return width


def truncate_text(text: str, max_width: float, font_size: float) -> str:
    if estimate_text_width(text, font_size) <= max_width:
        return text
    suffix = "…"
    allowed = max_width - estimate_text_width(suffix, font_size)
    result = ""
    for char in text:
        if estimate_text_width(result + char, font_size) > allowed:
            break
        result += char
    return result + suffix if result else suffix


def marker_svg(marker: str, x: float, y: float, color: str) -> str:
    common = f'fill="{color}" stroke="#FFFFFF" stroke-width="1.5"'
    if marker == "square":
        return f'<rect x="{x - 4:.2f}" y="{y - 4:.2f}" width="8" height="8" {common}/>'
    if marker == "diamond":
        return f'<path d="M {x:.2f} {y - 5:.2f} L {x + 5:.2f} {y:.2f} L {x:.2f} {y + 5:.2f} L {x - 5:.2f} {y:.2f} Z" {common}/>'
    if marker == "triangle":
        return f'<path d="M {x:.2f} {y - 5.5:.2f} L {x + 5.5:.2f} {y + 4:.2f} L {x - 5.5:.2f} {y + 4:.2f} Z" {common}/>'
    if marker == "cross":
        return (
            f'<path d="M {x - 4:.2f} {y:.2f} H {x + 4:.2f} M {x:.2f} {y - 4:.2f} '
            f'V {y + 4:.2f}" fill="none" stroke="{color}" stroke-width="2.5"/>'
        )
    if marker == "star":
        points = []
        for index in range(10):
            radius = 6 if index % 2 == 0 else 2.8
            angle = -math.pi / 2 + index * math.pi / 5
            points.append(f"{x + radius * math.cos(angle):.2f},{y + radius * math.sin(angle):.2f}")
        return f'<polygon points="{" ".join(points)}" {common}/>'
    return f'<circle cx="{x:.2f}" cy="{y:.2f}" r="4" {common}/>'


def svg_text(x: float, y: float, text: str, **attrs: str | float) -> str:
    rendered = " ".join(f'{key.replace("_", "-")}="{value}"' for key, value in attrs.items())
    return f'<text x="{x:.2f}" y="{y:.2f}" {rendered}>{html.escape(text)}</text>'


def render_svg(
    data: ChartData,
    *,
    plot_type: str,
    title: str,
    x_label: str,
    y_label: str,
    width: int,
    height: int,
) -> str:
    width = max(width, 640)
    height = max(height, 420)
    left = 106 if width >= 900 else 82
    right = 54
    top = 118
    bottom = 118
    chart_width = width - left - right
    chart_height = height - top - bottom

    all_values = [value for series in data.series for value in series.values if value is not None]
    y_min = min(all_values)
    y_max = max(all_values)
    if plot_type == "bar":
        y_min = min(y_min, 0.0)
        y_max = max(y_max, 0.0)
    y_ticks = nice_ticks(y_min, y_max, 6)
    y_min, y_max = y_ticks[0], y_ticks[-1]
    if abs(y_max - y_min) < 1e-12:
        y_max = y_min + 1

    valid_x = [value for value in data.x_values if value is not None]
    x_min = min(valid_x) if valid_x else 0.0
    x_max = max(valid_x) if valid_x else float(max(len(data.x_labels) - 1, 1))
    if abs(x_max - x_min) < 1e-12:
        x_min -= 0.5
        x_max += 0.5

    def sx(value: float) -> float:
        return left + (value - x_min) / (x_max - x_min) * chart_width

    def sy(value: float) -> float:
        return top + (y_max - value) / (y_max - y_min) * chart_height

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="chart-title chart-desc">',
        f"<title id=\"chart-title\">{html.escape(title)}</title>",
        '<desc id="chart-desc">'
        + html.escape(
            f"{plot_type} 图表，横轴为 {x_label}，纵轴包含 {', '.join(item.name for item in data.series)}。"
        )
        + "</desc>",
        """
        <style>
          .canvas { fill: #F8FAFC; }
          .plot-area { fill: #FFFFFF; }
          .grid { stroke: #E2E8F0; stroke-width: 1; }
          .axis { stroke: #64748B; stroke-width: 1.25; }
          .tick { fill: #475569; font-family: "Microsoft YaHei", "Segoe UI", Arial, sans-serif; font-size: 12px; }
          .axis-label { fill: #334155; font-family: "Microsoft YaHei", "Segoe UI", Arial, sans-serif; font-size: 14px; font-weight: 500; }
          .title { fill: #0F172A; font-family: "Microsoft YaHei", "Segoe UI", Arial, sans-serif; font-size: 24px; font-weight: 500; }
          .series-line { fill: none; stroke-width: 2.5; stroke-linejoin: round; stroke-linecap: round; }
          .bar { opacity: 0.9; }
          .legend-text { fill: #334155; font-family: "Microsoft YaHei", "Segoe UI", Arial, sans-serif; font-size: 13px; }
          .empty { fill: #64748B; font-family: "Microsoft YaHei", "Segoe UI", Arial, sans-serif; font-size: 14px; }
        </style>
        """,
        f'<rect class="canvas" x="0" y="0" width="{width}" height="{height}" rx="18"/>',
        svg_text(width / 2, 48, title, class_="title", text_anchor="middle"),
        f'<rect class="plot-area" x="{left}" y="{top}" width="{chart_width}" height="{chart_height}"/>',
    ]

    for tick in y_ticks:
        y = sy(tick)
        parts.append(f'<line class="grid" x1="{left}" y1="{y:.2f}" x2="{left + chart_width}" y2="{y:.2f}"/>')
        parts.append(svg_text(left - 14, y + 4, format_number(tick), class_="tick", text_anchor="end"))

    parts.extend(
        [
            f'<line class="axis" x1="{left}" y1="{top}" x2="{left}" y2="{top + chart_height}"/>',
            f'<line class="axis" x1="{left}" y1="{top + chart_height}" x2="{left + chart_width}" y2="{top + chart_height}"/>',
        ]
    )

    if data.x_is_numeric:
        x_ticks = nice_ticks(x_min, x_max, 7)
        x_tick_positions = [
            (max(0.0, min(sx(value), chart_width)) + left, format_number(value))
            for value in x_ticks
            if left - 1 <= sx(value) <= left + chart_width + 1
        ]
    else:
        label_count = len(data.x_labels)
        max_labels = max(3, min(12, chart_width // 88))
        if label_count <= max_labels:
            indices = list(range(label_count))
        else:
            indices = sorted(
                {
                    round(index * (label_count - 1) / (max_labels - 1))
                    for index in range(max_labels)
                }
            )
        x_tick_positions = [(sx(float(index)), data.x_labels[index]) for index in indices]

    for x, label in x_tick_positions:
        display = truncate_text(label, 110, 12)
        parts.append(f'<line class="axis" x1="{x:.2f}" y1="{top + chart_height}" x2="{x:.2f}" y2="{top + chart_height + 6}"/>')
        parts.append(
            svg_text(
                x,
                top + chart_height + 25,
                display,
                class_="tick",
                text_anchor="end",
                transform=f"rotate(-32 {x:.2f} {top + chart_height + 25:.2f})",
            )
        )

    parts.append(
        svg_text(
            left + chart_width / 2,
            height - 20,
            x_label,
            class_="axis-label",
            text_anchor="middle",
        )
    )
    parts.append(
        svg_text(
            24,
            top + chart_height / 2,
            y_label,
            class_="axis-label",
            text_anchor="middle",
            transform=f"rotate(-90 24 {top + chart_height / 2:.2f})",
        )
    )

    if plot_type == "bar":
        valid_count = sum(value is not None for value in data.series[0].values)
        group_width = chart_width / max(valid_count, 1) if not data.x_is_numeric else chart_width / max(len(data.x_labels), 1)
        bar_width = max(2.0, min(46.0, group_width * 0.72 / len(data.series)))
        zero_y = sy(0.0)
        for series_index, item in enumerate(data.series):
            color = SERIES_COLORS[series_index % len(SERIES_COLORS)]
            for point_index, value in enumerate(item.values):
                if value is None:
                    continue
                x_raw = data.x_values[point_index]
                x_center = sx(x_raw if x_raw is not None else float(point_index))
                offset = (series_index - (len(data.series) - 1) / 2) * bar_width
                top_y = sy(value)
                bar_y = min(top_y, zero_y)
                bar_height = max(abs(zero_y - top_y), 1.0)
                parts.append(
                    f'<rect class="bar" x="{x_center + offset - bar_width / 2:.2f}" y="{bar_y:.2f}" '
                    f'width="{bar_width:.2f}" height="{bar_height:.2f}" rx="2" fill="{color}">'
                    f"<title>{html.escape(item.name)}: {format_number(value)}</title></rect>"
                )
    else:
        for series_index, item in enumerate(data.series):
            color = SERIES_COLORS[series_index % len(SERIES_COLORS)]
            marker = SERIES_MARKERS[series_index % len(SERIES_MARKERS)]
            segments: list[list[tuple[float, float]]] = []
            current: list[tuple[float, float]] = []
            for point_index, value in enumerate(item.values):
                x_raw = data.x_values[point_index]
                if value is None or x_raw is None:
                    if current:
                        segments.append(current)
                        current = []
                    continue
                current.append((sx(x_raw), sy(value)))
            if current:
                segments.append(current)

            if plot_type == "line":
                for segment in segments:
                    if len(segment) > 1:
                        points = " ".join(f"{x:.2f},{y:.2f}" for x, y in segment)
                        parts.append(f'<polyline class="series-line" points="{points}" stroke="{color}"/>')
            for segment in segments:
                for x, y in segment:
                    parts.append(marker_svg(marker, x, y, color))

    legend_items = []
    for index, item in enumerate(data.series):
        color = SERIES_COLORS[index % len(SERIES_COLORS)]
        marker = SERIES_MARKERS[index % len(SERIES_MARKERS)]
        legend_items.append((item.name, color, marker))

    legend_font = 13
    item_widths = [min(estimate_text_width(name, legend_font) + 32, 220) for name, _, _ in legend_items]
    legend_total = sum(item_widths) + 12 * max(len(item_widths) - 1, 0)
    if legend_total > chart_width:
        legend_items = legend_items[: max(1, chart_width // 120)]
        item_widths = item_widths[: len(legend_items)]
        legend_total = sum(item_widths) + 12 * max(len(item_widths) - 1, 0)
    legend_x = left + max(0.0, chart_width - legend_total)
    legend_y = 79
    for (name, color, marker), item_width in zip(legend_items, item_widths):
        if plot_type == "line":
            parts.append(f'<line x1="{legend_x:.2f}" y1="{legend_y - 4}" x2="{legend_x + 22:.2f}" y2="{legend_y - 4}" stroke="{color}" stroke-width="2.5"/>')
            parts.append(marker_svg(marker, legend_x + 11, legend_y - 4, color))
        else:
            parts.append(marker_svg(marker if plot_type == "scatter" else "square", legend_x + 8, legend_y - 4, color))
        display = truncate_text(name, item_width - 32, legend_font)
        parts.append(svg_text(legend_x + 30, legend_y, display, class_="legend-text"))
        legend_x += item_width + 12

    if not all_values:
        parts.append(svg_text(width / 2, top + chart_height / 2, "没有可绘制的有效数值", class_="empty", text_anchor="middle"))
    parts.append("</svg>")
    return "\n".join(parts)


def wrap_as_html(svg: str, title: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    body {{ margin: 0; min-height: 100vh; display: grid; place-items: center; background: #EEF2F7; }}
    svg {{ width: min(100vw, 1200px); height: auto; box-shadow: 0 16px 48px rgb(15 23 42 / 12%); }}
  </style>
</head>
<body>
{svg}
</body>
</html>
"""


def demo_data() -> tuple[list[str], list[dict[str, str]]]:
    columns = ["月份", "销售额", "成本"]
    values = [
        ("1月", 42, 28),
        ("2月", 48, 31),
        ("3月", 45, 30),
        ("4月", 57, 34),
        ("5月", 63, 38),
        ("6月", 69, 41),
        ("7月", 74, 44),
        ("8月", 81, 48),
        ("9月", 77, 46),
        ("10月", 88, 52),
        ("11月", 96, 55),
        ("12月", 103, 59),
    ]
    rows = [
        {"月份": month, "销售额": str(sales), "成本": str(cost)}
        for month, sales, cost in values
    ]
    return columns, rows


def main() -> int:
    args = parse_args()
    if args.demo:
        columns, rows = demo_data()
        input_name = "内置示例"
        args.title = "月度经营数据" if args.title == "数据图" else args.title
        args.xlabel = "月份" if args.xlabel is None else args.xlabel
        args.ylabel = "金额（万元）" if args.ylabel is None else args.ylabel
        x_column = "月份"
        y_columns = ["销售额", "成本"]
    else:
        if not args.input:
            print("错误: 请提供输入文件，或使用 --demo 查看示例。", file=sys.stderr)
            return 2
        input_path = Path(args.input).expanduser()
        if not input_path.is_file():
            print(f"错误: 找不到输入文件: {input_path}", file=sys.stderr)
            return 2
        if input_path.suffix.lower() in {".xlsx", ".xls"}:
            print("错误: 当前脚本读取 CSV/TSV；请先将 Excel 另存为 CSV。", file=sys.stderr)
            return 2
        try:
            columns, rows = read_table(input_path, args.encoding, args.delimiter)
        except (OSError, ValueError) as exc:
            print(f"错误: {exc}", file=sys.stderr)
            return 2
        input_name = str(input_path)
        x_column = args.x
        y_columns = args.y
        if args.list_columns:
            print("列名:")
            for column in columns:
                numeric_mark = "（数值）" if is_numeric_column(rows, column) else ""
                print(f"  - {column}{numeric_mark}")
            return 0

    if args.sort_x:
        rows = sort_rows(rows, x_column or columns[0])
    try:
        data = build_chart_data(columns, rows, x_column, y_columns)
    except ValueError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2

    x_label = args.xlabel or (x_column or columns[0])
    y_label = args.ylabel or (
        data.series[0].name if len(data.series) == 1 else "数值"
    )
    svg = render_svg(
        data,
        plot_type=args.type,
        title=args.title,
        x_label=x_label,
        y_label=y_label,
        width=args.width,
        height=args.height,
    )

    # --demo 没有输入文件时，用户常把第一个位置参数直接写为图片路径。
    if args.demo and args.input and args.output == "plot.svg" and not args.output_file:
        output_path = Path(args.input).expanduser()
    else:
        output_path = Path(args.output_file or args.output).expanduser()
    suffix = output_path.suffix.lower()
    if suffix not in {".svg", ".html"}:
        print("错误: 输出文件扩展名必须是 .svg 或 .html。", file=sys.stderr)
        return 2
    content = wrap_as_html(svg, args.title) if suffix == ".html" else svg
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")
    except OSError as exc:
        print(f"错误: 无法写入输出文件: {exc}", file=sys.stderr)
        return 2

    print(f"绘图完成: {output_path.resolve()}")
    print(f"数据源: {input_name}")
    print(f"图形类型: {args.type}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
