"""
importer.py — 讀取出版社／廠商寄來的建檔檔案，依商品種類整理成資料庫要的欄位。

欄位別名設定在 config.py；遇到新的欄位名稱，加到對應欄位的 aliases 就能自動辨識。
"""
import io

import pandas as pd

import db


def _key(name) -> str:
    return db.normalize(name).replace(" ", "")


def read_raw(file_bytes: bytes, filename: str, sheet=None) -> pd.DataFrame:
    """不指定表頭、全部當文字讀進來（避免 ISBN / JAN 被當成數字而變形）。"""
    if filename.lower().endswith(".csv"):
        for enc in ("utf-8-sig", "cp950", "big5hkscs", "cp932", "utf-16"):
            try:
                return pd.read_csv(io.BytesIO(file_bytes), header=None, dtype=str, encoding=enc)
            except (UnicodeDecodeError, UnicodeError):
                continue
        raise ValueError("無法辨識這個 CSV 的文字編碼，請另存成 UTF-8 或 Excel 格式再上傳。")
    return pd.read_excel(io.BytesIO(file_bytes), header=None, dtype=str, sheet_name=sheet or 0)


def sheet_names(file_bytes: bytes, filename: str) -> list[str]:
    if filename.lower().endswith(".csv"):
        return []
    return pd.ExcelFile(io.BytesIO(file_bytes)).sheet_names


def detect_header_row(raw: pd.DataFrame, cat, scan: int = 30) -> int:
    """前幾列中命中最多欄位別名的那一列就是表頭。回傳 0 起算的列號。"""
    all_aliases = {_key(a) for f in cat["fields"] for a in f["aliases"]}
    best_row, best_hits = 0, 0
    for i in range(min(scan, len(raw))):
        hits = sum(1 for v in raw.iloc[i].tolist() if _key(v) in all_aliases)
        if hits > best_hits:
            best_row, best_hits = i, hits
    return best_row


def apply_header(raw: pd.DataFrame, header_row: int) -> pd.DataFrame:
    headers, seen = [], {}
    for i, v in enumerate(raw.iloc[header_row].tolist()):
        h = db.clean_text(v) or f"(第{i + 1}欄)"
        if h in seen:
            seen[h] += 1
            h = f"{h}_{seen[h]}"
        else:
            seen[h] = 0
        headers.append(h)
    df = raw.iloc[header_row + 1:].copy()
    df.columns = headers
    return df.dropna(how="all")          # 保留原本的列號，方便對照 Excel


def guess_mapping(columns: list[str], cat) -> dict:
    """回傳 {欄位 key: 檔案中的欄名 或 None}"""
    keyed = {_key(c): c for c in columns}
    return {
        f["key"]: next((keyed[_key(a)] for a in f["aliases"] if _key(a) in keyed), None)
        for f in cat["fields"]
    }


def build_records(df: pd.DataFrame, mapping: dict, cat, fill_values: dict | None = None):
    """回傳 (records, 被略過的列 DataFrame)"""
    fill_values = fill_values or {}
    pk = db.key_field(cat)
    labels = {f["key"]: f["label"] for f in cat["fields"]}
    records, skipped = {}, []

    for idx, row in df.iterrows():
        rec = {}
        for f in cat["fields"]:
            col = mapping.get(f["key"])
            raw = row[col] if col else None
            val = db.CLEANERS[f["kind"]](raw) if col else ""
            rec[f["key"]] = val or fill_values.get(f["key"], "").strip()

        missing = [k for k in cat["required"] if not rec.get(k)]
        if missing:
            first_col = mapping.get(pk)
            skipped.append({
                "Excel 列號": idx + 1,
                f"原始 {labels[pk]}": db.clean_text(row[first_col]) if first_col else "",
                "原因": "、".join(f"{labels[k]}無法辨識或空白" for k in missing),
            })
            continue
        records[rec[pk]] = rec          # 同一檔案內重複的碼，以後出現的為準
    return list(records.values()), pd.DataFrame(skipped)
