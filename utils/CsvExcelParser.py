import os
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd

from models import Document
from utils.error_codes import BizCode


TableData = Tuple[str, List[str], List[Dict[str, str]]]


class CsvExcelParser:
    SUPPORTED_FORMATS = {".csv", ".xlsx", ".xls", ".xlsm"}

    TITLE_KEYS = [
        "title", "name", "subject", "case",
        "\u6807\u9898", "\u6848\u4f8b", "\u540d\u79f0",
    ]
    PROBLEM_KEYS = [
        "problem", "issue", "fault", "symptom", "description", "desc",
        "\u95ee\u9898", "\u73b0\u8c61", "\u6545\u969c", "\u63cf\u8ff0",
    ]
    CAUSE_KEYS = [
        "cause", "reason", "root",
        "\u539f\u56e0", "\u6839\u56e0",
    ]
    EVALUATION_KEYS = [
        "evaluation", "assess", "score", "result", "status", "state",
        "\u8bc4\u4f30", "\u8bc4\u5206", "\u7ed3\u679c",
    ]
    INSPECTION_KEYS = [
        "inspection", "check", "verify", "step", "process",
        "\u68c0\u67e5", "\u68c0\u9a8c", "\u68c0\u6d4b", "\u6b65\u9aa4", "\u6d41\u7a0b",
    ]
    SOLUTION_KEYS = [
        "solution", "action", "repair", "fix", "mitigation", "recommendation",
        "\u89e3\u51b3", "\u5904\u7406", "\u63aa\u65bd", "\u65b9\u6848", "\u5efa\u8bae",
    ]
    KEY_POINT_KEYS = [
        "summary", "key", "conclusion", "remark", "note",
        "\u91cd\u70b9", "\u7ed3\u8bba", "\u603b\u7ed3", "\u5907\u6ce8",
    ]

    def __init__(self):
        self.last_error_code = None
        self.last_error_detail = None

    def _set_last_error(self, code: int, message: str):
        self.last_error_code = int(code)
        self.last_error_detail = message

    def _clean_text(self, value) -> str:
        if value is None:
            return ""
        text = str(value).strip()
        if text.lower() in {"nan", "none", "null"}:
            return ""
        return text

    def parse(self, file_path: str):
        self.last_error_code = None
        self.last_error_detail = None
        try:
            suffix = Path(file_path).suffix.lower()
            if suffix not in self.SUPPORTED_FORMATS:
                self._set_last_error(BizCode.DOC_FILE_TYPE_UNSUPPORTED, f"unsupported file type: {suffix}")
                return None
            if not os.path.exists(file_path):
                self._set_last_error(BizCode.DOC_RESOURCE_NOT_FOUND, f"file not found: {file_path}")
                return None

            tables = self._load_tables(file_path, suffix)
            if not tables:
                self._set_last_error(BizCode.DOC_PARSE_FAILED, "no readable sheet or rows in table file")
                return None

            return self._tables_to_document(file_path, tables)
        except Exception as e:
            self._set_last_error(BizCode.DOC_PARSE_FAILED, str(e))
            return None

    def _load_tables(self, file_path: str, suffix: str) -> List[TableData]:
        if suffix == ".csv":
            table = self._read_csv_by_pandas(file_path)
            return [table] if table else []
        if suffix in {".xlsx", ".xls", ".xlsm"}:
            return self._read_excel_by_pandas(file_path)
        return []

    def _frame_to_table(self, sheet_name: str, frame) -> TableData | None:
        if frame is None:
            return None
        frame = frame.dropna(axis=0, how="all").dropna(axis=1, how="all")
        if frame.empty and len(frame.columns) == 0:
            return None
        columns = [self._clean_text(c) or f"column_{i + 1}" for i, c in enumerate(frame.columns)]
        rows: List[Dict[str, str]] = []
        for _, row in frame.head(2000).iterrows():
            item: Dict[str, str] = {}
            for i, col in enumerate(frame.columns):
                item[columns[i]] = self._clean_text(row.get(col))
            if any(v for v in item.values()):
                rows.append(item)
        return str(sheet_name), columns, rows

    def _read_csv_by_pandas(self, file_path: str) -> TableData | None:
        last_error = None
        for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
            try:
                frame = pd.read_csv(file_path, dtype=str, encoding=enc)
                return self._frame_to_table("csv", frame)
            except Exception as e:
                last_error = e
        if last_error:
            raise last_error
        return None

    def _read_excel_by_pandas(self, file_path: str) -> List[TableData]:
        workbook = pd.read_excel(file_path, sheet_name=None, dtype=str)
        tables: List[TableData] = []
        for sheet_name, frame in workbook.items():
            table = self._frame_to_table(str(sheet_name), frame)
            if table:
                tables.append(table)
        return tables

    def _collect_by_keys(
        self,
        tables: List[TableData],
        keys: List[str],
        max_items: int = 8,
        max_len_per_item: int = 120,
    ) -> str:
        results: List[str] = []
        seen = set()
        keys_lower = [k.lower() for k in keys]

        for sheet_name, columns, rows in tables:
            if not rows:
                continue
            target_cols = [c for c in columns if any(k in c.lower() for k in keys_lower)]
            for col in target_cols:
                for row in rows:
                    text = self._clean_text(row.get(col))
                    if not text:
                        continue
                    if len(text) > max_len_per_item:
                        text = text[:max_len_per_item] + "..."
                    unique_key = text.lower()
                    if unique_key in seen:
                        continue
                    seen.add(unique_key)
                    results.append(f"[{sheet_name}:{col}] {text}")
                    if len(results) >= max_items:
                        return "; ".join(results)
        return "; ".join(results)

    def _sample_rows_text(
        self,
        rows: List[Dict[str, str]],
        columns: List[str],
        max_rows: int = 3,
        max_cols: int = 6,
    ) -> str:
        if not rows:
            return ""
        samples: List[str] = []
        cols = columns[:max_cols]
        for row in rows[:max_rows]:
            kvs = []
            for col in cols:
                val = self._clean_text(row.get(col))
                if not val:
                    continue
                if len(val) > 60:
                    val = val[:60] + "..."
                kvs.append(f"{col}={val}")
            if kvs:
                samples.append("; ".join(kvs))
        return " | ".join(samples)

    def _table_overview(self, tables: List[TableData]) -> str:
        lines = [f"Parsed {len(tables)} table(s)."]
        for sheet_name, columns, rows in tables[:6]:
            col_text = ", ".join(columns[:12])
            lines.append(f"Sheet[{sheet_name}] rows={len(rows)}, cols={len(columns)}, columns={col_text}")
            sample = self._sample_rows_text(rows, columns)
            if sample:
                lines.append(f"Sheet[{sheet_name}] samples: {sample}")
        return "\n".join(lines)

    def _build_title(self, file_path: str, tables: List[TableData]) -> str:
        title = self._collect_by_keys(tables, self.TITLE_KEYS, max_items=1, max_len_per_item=80)
        if title:
            title = title.split("] ", 1)[-1].strip()
        if title:
            return title
        return f"Table Parse - {Path(file_path).stem}"

    def _tables_to_document(self, file_path: str, tables: List[TableData]):
        title = self._build_title(file_path, tables)
        problem_intro = self._collect_by_keys(tables, self.PROBLEM_KEYS)
        causes = self._collect_by_keys(tables, self.CAUSE_KEYS)
        evaluation = self._collect_by_keys(tables, self.EVALUATION_KEYS)
        inspection = self._collect_by_keys(tables, self.INSPECTION_KEYS)
        solutions = self._collect_by_keys(tables, self.SOLUTION_KEYS)
        key_points = self._collect_by_keys(tables, self.KEY_POINT_KEYS)

        overview = self._table_overview(tables)
        if problem_intro:
            problem_intro = f"{problem_intro}\n{overview}"
        else:
            problem_intro = overview
        if not key_points:
            key_points = overview

        payload = {
            "title": title,
            "problem_intro": problem_intro,
            "image_urls": None,
            "causes": causes,
            "image_urls_causes": None,
            "evaluation": evaluation,
            "image_urls_evaluation": None,
            "inspection": inspection,
            "image_urls_inspection": None,
            "solutions": solutions,
            "image_urls_solutions": None,
            "key_points": key_points,
            "image_urls_problem_intro": None,
            "image_urls_key_points": None,
        }
        return Document(**payload, is_vectorized=0)


csv_excel_parser = CsvExcelParser()
