"""Deterministic Excel records with explicit column and source-row provenance."""
from pathlib import Path


def read_groups(file_path, max_rows=20, max_chars=4000):
    import openpyxl
    from openpyxl.utils import get_column_letter
    book = openpyxl.load_workbook(file_path, data_only=True, read_only=False)
    groups = []
    try:
        for sheet in book.worksheets:
            # Preserve empty column positions; merged cells inherit their anchor value.
            rows = {}
            for row in sheet.iter_rows():
                values = {cell.column: str(cell.value).strip() for cell in row
                          if cell.value is not None and str(cell.value).strip()}
                if values:
                    rows[row[0].row] = values
            if not rows:
                continue
            for merged in sheet.merged_cells.ranges:
                value = rows.get(merged.min_row, {}).get(merged.min_col)
                if value:
                    for r in range(merged.min_row, merged.max_row + 1):
                        for c in range(merged.min_col, merged.max_col + 1):
                            rows.setdefault(r, {})[c] = value
            columns = sorted({c for values in rows.values() for c in values})
            ordered = sorted(rows)
            # Skip leading single-cell titles when a multi-column header follows.
            header_row = next((r for r in ordered[:20] if len(set(rows[r].values())) >= 2), ordered[0])
            header_end = header_row
            for merged in sheet.merged_cells.ranges:
                if merged.min_row == header_row and merged.max_row > header_end:
                    header_end = merged.max_row
                if merged.min_row == header_row and merged.max_col > merged.min_col and header_row + 1 in rows:
                    header_end = max(header_end, header_row + 1)
            headers = {}
            for c in columns:
                labels = list(dict.fromkeys(rows[r][c] for r in range(header_row, header_end + 1)
                                            if c in rows.get(r, {})))
                label = ' / '.join(labels) or '列' + get_column_letter(c)
                headers[c] = label
            duplicates = {label for label in headers.values() if list(headers.values()).count(label) > 1}
            headers = {c: f'{label}（{get_column_letter(c)}列）' if label in duplicates else label
                       for c, label in headers.items()}
            preamble = '\n'.join('；'.join(rows[r].values()) for r in ordered if r < header_row)
            batch, length = [], 0
            def flush():
                if batch:
                    groups.append(dict(sheet_name=sheet.title, headers=list(headers.values()),
                        header_rows=list(range(header_row, header_end + 1)), preamble=preamble,
                        records=list(batch), row_start=batch[0]['row'], row_end=batch[-1]['row']))
                    batch.clear()
            for r in ordered:
                if r <= header_end:
                    continue
                text = f'原始行号：{r}\n' + '\n'.join(f'{headers[c]}：{rows[r].get(c, "")}' for c in columns)
                if batch and (len(batch) >= max_rows or length + len(text) > max_chars):
                    flush()
                    length = 0
                batch.append({'row': r, 'text': text, 'values': [rows[r].get(c, '') for c in columns]})
                length += len(text)
            flush()
            if not any(g['sheet_name'] == sheet.title for g in groups):
                groups.append(dict(sheet_name=sheet.title, headers=list(headers.values()),
                    header_rows=list(range(header_row, header_end + 1)), preamble=preamble,
                    records=[], row_start=header_row, row_end=header_end))
    finally:
        book.close()
    return groups
