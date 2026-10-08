"""
importer.py — 讀取出版社／廠商寄來的建檔檔案，依商品種類整理成資料庫要的欄位。

欄位別名設定在 config.py；遇到新的欄位名稱，加到對應欄位的 aliases 就能自動辨識。
"""
import csv
import io
import re

import pandas as pd

import db


def _key(name) -> str:
    return db.normalize(name).replace(" ", "")


def read_pasted(text: str) -> pd.DataFrame:
    """
    讀貼上的文字。從 Excel／Google 試算表複製貼上時欄位用 Tab 分隔；
    沒有 Tab 時改用逗號，再沒有就用連續兩個以上的空格。
    """
    lines = [ln for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n") if ln.strip()]
    if not lines:
        return pd.DataFrame()
    sample = "\n".join(lines[:20])
    if "\t" in sample:
        rows = [ln.split("\t") for ln in lines]
    elif "," in sample:
        rows = list(csv.reader(lines))
    else:
        rows = [re.split(r"\s{2,}|\u3000", ln.strip()) for ln in lines]
    width = max(len(r) for r in rows)
    rows = [[c.strip() or None for c in r] + [None] * (width - len(r)) for r in rows]
    return pd.DataFrame(rows, dtype=str)


def read_raw(file_bytes: bytes, filename: str, sheet=None) -> pd.DataFrame:
    """不指定表頭、全部當文字讀進來（避免 ISBN / JAN 被當成數字而變形）。"""
    if filename.lower().endswith(".txt"):          # 貼上的文字
        return read_pasted(file_bytes.decode("utf-8"))
    if filename.lower().endswith(".csv"):
        for enc in ("utf-8-sig", "cp950", "big5hkscs", "cp932", "utf-16"):
            try:
                return pd.read_csv(io.BytesIO(file_bytes), header=None, dtype=str, encoding=enc)
            except (UnicodeDecodeError, UnicodeError):
                continue
        raise ValueError("無法辨識這個 CSV 的文字編碼，請另存成 UTF-8 或 Excel 格式再上傳。")
    return pd.read_excel(io.BytesIO(file_bytes), header=None, dtype=str, sheet_name=sheet or 0)


def sheet_names(file_bytes: bytes, filename: str) -> list[str]:
    if filename.lower().endswith((".csv", ".txt")):
        return []
    return pd.ExcelFile(io.BytesIO(file_bytes)).sheet_names


def has_header(raw: pd.DataFrame, cat, scan: int = 30) -> bool:
    """前幾列裡有沒有任何一格是已知的欄位名稱。"""
    all_aliases = {_key(a) for f in cat["fields"] for a in f["aliases"]}
    return any(_key(v) in all_aliases
               for i in range(min(scan, len(raw))) for v in raw.iloc[i].tolist())


def no_header(raw: pd.DataFrame) -> pd.DataFrame:
    """沒有欄位名稱列時：欄名用「第1欄、第2欄…」，所有列都當資料。"""
    df = raw.copy()
    df.columns = [f"第{i + 1}欄" for i in range(len(df.columns))]
    return df.dropna(how="all")


def guess_mapping_by_content(df: pd.DataFrame, cat) -> dict:
    """
    沒有欄位名稱時，看內容猜欄位：
    碼（ISBN/JAN）＝大多數格子能辨識成碼的欄；價格＝大多是 1～5 位數字的欄；
    其餘文字欄依「平均長度」由長到短，依序對應設定裡的文字欄位（名稱通常最長）。
    """
    sample = df.head(50)
    mapping = {f["key"]: None for f in cat["fields"] + cat.get("append", [])}
    used = set()

    def ratio(col, test):
        vals = [v for v in sample[col].tolist() if db.clean_text(v)]
        return sum(1 for v in vals if test(v)) / len(vals) if vals else 0

    for f in cat["fields"]:
        if f["kind"] in ("isbn", "jan"):
            best = max((c for c in df.columns if c not in used),
                       key=lambda c: ratio(c, lambda v: db.CLEANERS[f["kind"]](v)), default=None)
            if best and ratio(best, lambda v: db.CLEANERS[f["kind"]](v)) >= 0.6:
                mapping[f["key"]] = best
                used.add(best)
    for f in cat["fields"]:
        if f["kind"] == "price":
            test = lambda v: re.fullmatch(r"\d{1,5}(\.0+)?", db.clean_price(v) or "") is not None
            best = max((c for c in df.columns if c not in used), key=lambda c: ratio(c, test), default=None)
            if best and ratio(best, test) >= 0.6:
                mapping[f["key"]] = best
                used.add(best)

    def avg_len(col):
        vals = [db.clean_text(v) for v in sample[col].tolist() if db.clean_text(v)]
        return sum(len(v) for v in vals) / len(vals) if vals else 0

    text_cols = sorted((c for c in df.columns if c not in used and avg_len(c) > 0),
                       key=avg_len, reverse=True)
    text_fields = [f["key"] for f in cat["fields"] if f["kind"] == "text"]
    if text_cols and text_fields:                 # 最長的欄位給名稱（書名／商品名稱）
        mapping[text_fields[0]] = text_cols[0]
    return mapping


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
    """回傳 {欄位 key: 檔案中的欄名 或 None}（含 append 附加欄位）"""
    keyed = {_key(c): c for c in columns}
    return {
        f["key"]: next((keyed[_key(a)] for a in f["aliases"] if _key(a) in keyed), None)
        for f in cat["fields"] + cat.get("append", [])
    }


def _clean_volume(raw) -> str:
    """「05」「5.0」→「5」；「上」「完」「特裝版」之類原樣保留。"""
    v = db.clean_text(raw)
    t = db.normalize(v)
    if re.fullmatch(r"\d+(\.0+)?", t):
        return str(int(float(t)))
    return v


def append_value(base: str, extra: str) -> str:
    """把 extra 接在 base 後面（空一格）；base 已經以它結尾就不重複接。"""
    if not extra:
        return base
    tail = db.normalize(base).replace(" ", "")
    if re.search(rf"(?<!\d){re.escape(db.normalize(extra).replace(' ', ''))}$", tail):
        return base
    return f"{base} {extra}"


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

        for a in cat.get("append", []):
            col = mapping.get(a["key"])
            if col and rec.get(a["into"]):
                rec[a["into"]] = append_value(rec[a["into"]], _clean_volume(row[col]))

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
