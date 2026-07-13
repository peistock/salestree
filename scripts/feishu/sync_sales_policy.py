#!/usr/bin/env python3
"""同步飞书销售政策表到本地 JSON。

用法：
    python3 scripts/feishu/sync_sales_policy.py

从 .env 读取飞书表格配置：
    SALES_POLICY_SPREADSHEET_TOKEN=xxx

默认表格链接（可被环境变量覆盖）：
    https://ecsage2.feishu.cn/sheets/YB33sS2OyhTdxytV5vlcHVrBnAb

输出：
    data/sales_policies.json

说明：
- 不再硬编码某个 sheet/range，而是读取整个工作簿的所有 sheet。
- 尽量保留原表格的行列结构与术语（返货/返现/返点等原样保留）。
- 前端按 sheet 标签页展示为接近原表的表格视图。
"""
import argparse
import csv
import io
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env", override=True)

LARK_CLI = os.path.expanduser("~/.nvm/versions/node/v20.20.0/bin/lark-cli")
OUTPUT_PATH = PROJECT_ROOT / "data" / "sales_policies.json"

DEFAULT_SPREADSHEET_TOKEN = "YB33sS2OyhTdxytV5vlcHVrBnAb"
DEFAULT_SHEET_NAME = "汇总"


def run_lark(args: list[str]) -> dict:
    if not Path(LARK_CLI).exists():
        raise FileNotFoundError(f"找不到 lark-cli: {LARK_CLI}")
        raise FileNotFoundError(f"找不到 lark-cli: {LARK_CLI}")

    cmd = [LARK_CLI] + args
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        print(f"[sync_sales_policy] lark-cli 失败: {result.stderr}", file=sys.stderr)
        raise RuntimeError(f"lark-cli 退出码 {result.returncode}: {result.stderr}")

    data = json.loads(result.stdout)
    if not data.get("ok"):
        raise RuntimeError(f"lark-cli 返回错误: {data}")
    return data["data"]


def col_letter(n: int) -> str:
    """1 -> A, 27 -> AA."""
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def range_for_sheet(sheet: dict) -> str:
    col = col_letter(sheet["column_count"])
    row = sheet["row_count"]
    return f"A1:{col}{row}"


def list_sheets(spreadsheet_token: str) -> list[dict]:
    data = run_lark(["sheets", "+workbook-info", "--spreadsheet-token", spreadsheet_token])
    return data.get("sheets", [])


def fetch_sheet_csv(spreadsheet_token: str, sheet_id: str, range_str: str) -> str:
    data = run_lark([
        "sheets", "+csv-get",
        "--spreadsheet-token", spreadsheet_token,
        "--sheet-id", sheet_id,
        "--range", range_str,
        "--format", "csv",
        "--include-row-prefix=false",
    ])
    return data["annotated_csv"]


def parse_csv_rows(csv_text: str) -> list[list[str]]:
    reader = csv.reader(io.StringIO(csv_text))
    return [[cell.strip() for cell in row] for row in reader]


def is_empty_row(row: list[str]) -> bool:
    return all(not cell for cell in row)


def trim_trailing_empty(rows: list[list[str]]) -> list[list[str]]:
    while rows and is_empty_row(rows[-1]):
        rows.pop()
    return rows


def trim_trailing_empty_columns(rows: list[list[str]]) -> list[list[str]]:
    if not rows:
        return rows
    max_col = 0
    for row in rows:
        for i in range(len(row) - 1, -1, -1):
            if row[i]:
                max_col = max(max_col, i + 1)
                break
    return [row[:max_col] for row in rows]


def extract_effective_date(rows: list[list[str]]) -> str:
    """在所有单元格中搜索 'YYYY年MM月DD日' 或 'YYYY-MM-DD'。"""
    for row in rows:
        for cell in row:
            m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", cell or "")
            if m:
                y, mo, d = m.groups()
                return f"{y}-{int(mo):02d}-{int(d):02d}"
            m2 = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", cell or "")
            if m2:
                y, mo, d = m2.groups()
                return f"{y}-{int(mo):02d}-{int(d):02d}"
    return ""


def detect_header_row_count(rows: list[list[str]]) -> int:
    """识别表头行数：从顶部开始，遇到第一条看起来像数据的行就停止。"""
    if not rows:
        return 0

    # 汇总表常见表头关键词
    first_line = " ".join(rows[0]).lower()
    if any(k in first_line for k in ["媒体", "赛道", "折扣形式", "代运营", "走量", "收量"]):
        return 1

    # 原始多 sheet 表格的数据行特征
    data_patterns = [
        r"^竞价.*类",
        r"^K[123]",
        r"^巨量品牌",
        r"^千川",
    ]
    for i, row in enumerate(rows):
        first = row[0] if row else ""
        for pat in data_patterns:
            if re.search(pat, first or ""):
                return i
    # 默认取前 4 行（与原始各 sheet 一致）
    return min(4, len(rows))


def extract_notes(rows: list[list[str]]) -> str:
    """收集以 '补充说明' 开头的单元格内容（可能跨多行）。"""
    notes_lines = []
    collecting = False
    for row in rows:
        if not row:
            continue
        first = row[0]
        if collecting:
            notes_lines.append(first)
            if first.endswith('"'):
                collecting = False
            continue
        if first.startswith('"补充说明') or first.startswith("补充说明"):
            collecting = True
            notes_lines.append(first)
            if first.endswith('"'):
                collecting = False
    notes = "\n".join(notes_lines).strip().strip('"').strip()
    return notes


def process_sheet(spreadsheet_token: str, sheet: dict) -> dict:
    sheet_id = sheet["sheet_id"]
    sheet_name = sheet["sheet_name"]
    range_str = range_for_sheet(sheet)

    print(f"[sync_sales_policy] 读取 sheet '{sheet_name}' ({sheet_id}) {range_str}")
    csv_text = fetch_sheet_csv(spreadsheet_token, sheet_id, range_str)
    rows = parse_csv_rows(csv_text)
    rows = trim_trailing_empty(rows)
    rows = trim_trailing_empty_columns(rows)

    effective_date = extract_effective_date(rows)
    notes = extract_notes(rows)
    header_row_count = detect_header_row_count(rows)

    return {
        "id": sheet_id,
        "name": sheet_name,
        "effective_date": effective_date,
        "notes": notes,
        "header_row_count": header_row_count,
        "row_count": len(rows),
        "column_count": max((len(r) for r in rows), default=0),
        "rows": rows,
    }


def main():
    parser = argparse.ArgumentParser(description="同步飞书销售政策表")
    parser.add_argument("--spreadsheet-token", default=os.getenv("SALES_POLICY_SPREADSHEET_TOKEN", DEFAULT_SPREADSHEET_TOKEN))
    parser.add_argument("--sheet-name", default=os.getenv("SALES_POLICY_SHEET_NAME", DEFAULT_SHEET_NAME), help="只同步指定名称的 sheet，留空则同步全部")
    parser.add_argument("--output", default=str(OUTPUT_PATH))
    args = parser.parse_args()

    print(f"[sync_sales_policy] 读取工作簿 {args.spreadsheet_token}")
    sheets = list_sheets(args.spreadsheet_token)
    if not sheets:
        raise RuntimeError("工作簿没有 sheet")

    if args.sheet_name:
        sheets = [s for s in sheets if s.get("sheet_name") == args.sheet_name]
        if not sheets:
            raise RuntimeError(f"找不到 sheet '{args.sheet_name}'")
        print(f"[sync_sales_policy] 只同步 sheet '{args.sheet_name}'")

    processed = []
    for sheet in sheets:
        try:
            processed.append(process_sheet(args.spreadsheet_token, sheet))
        except Exception as e:
            print(f"[sync_sales_policy] sheet '{sheet.get('sheet_name')}' 同步失败: {e}", file=sys.stderr)

    data = {
        "updatedAt": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        "spreadsheetToken": args.spreadsheet_token,
        "spreadsheetUrl": f"https://ecsage2.feishu.cn/sheets/{args.spreadsheet_token}",
        "sheets": processed,
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"[sync_sales_policy] 已同步 {len(processed)} 个 sheet 到 {output_path}")


if __name__ == "__main__":
    main()
