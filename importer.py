"""
importer.py — 讀取出版社寄來的建檔檔案，整理成資料庫要的四個欄位。

每家出版社欄位名稱不同，這裡用「別名表」自動對應；
對應不到的，在網頁上用下拉選單手動指定即可。
發現新的欄位名稱時，把它加進 COLUMN_ALIASES 就能自動辨識。
"""
import io

import pandas as pd

import db

# 標準欄位 → 各出版社可能使用的欄位名稱（比對時忽略空白與大小寫）
COLUMN_ALIASES = {
    "isbn": ["isbn", "isbn13", "isbn-13", "國際標準書號", "書號", "條碼", "國際條碼", "ean", "barcode"],
    "title": ["書名", "品名", "商品名稱", "書籍名稱", "中文書名", "title", "品項名稱"],
    "author": ["作者", "著者", "作者名", "作/繪者", "作繪者", "原作", "author"],
    "price": ["定價", "定價(元)", "定價（元）", "售價", "原價", "價格", "建議售價", "price"],
    "publisher": ["出版社", "出版者", "出版公司", "發行", "發行者", "publisher"],
}
FIELD_LABELS = {"isbn": "ISBN", "title": "書名", "author": "作者", "price": "定價", "publisher": "出版社"}
FIELDS = list(FIELD_LABELS)


def _key(name) -> str:
    return db.normalize(name).replace(" ", "")


def read_raw(file_bytes: bytes, filename: str, sheet=None) -> pd.DataFrame:
    """不指定表頭、全部當文字讀進來（避免 ISBN 被當成數字而變形）。"""
    name = filename.lower()
    if name.endswith(".csv"):
        for enc in ("utf-8-sig", "cp950", "big5hkscs", "utf-16"):
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


def detect_header_row(raw: pd.DataFrame, scan: int = 30) -> int:
    """在前幾列中，找出最像表頭的那一列（命中最多別名的列）。回傳 0 起算的列號。"""
    all_aliases = {_key(a) for aliases in COLUMN_ALIASES.values() for a in aliases}
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
        if h in seen:                      # 欄名重複時加編號區分
            seen[h] += 1
            h = f"{h}_{seen[h]}"
        else:
            seen[h] = 0
        headers.append(h)
    df = raw.iloc[header_row + 1:].copy()
    df.columns = headers
    return df.dropna(how="all")          # 保留原本的列號，方便對照 Excel


def guess_mapping(columns: list[str]) -> dict:
    """回傳 {標準欄位: 檔案中的欄名 或 None}"""
    mapping = {}
    keyed = {_key(c): c for c in columns}
    for field, aliases in COLUMN_ALIASES.items():
        mapping[field] = next((keyed[_key(a)] for a in aliases if _key(a) in keyed), None)
    return mapping


def build_records(df: pd.DataFrame, mapping: dict, fixed_publisher: str = ""):
    """
    依對應整理成資料庫用的 records。
    回傳 (records, 被略過的列 DataFrame)
    """
    records, skipped = {}, []
    for idx, row in df.iterrows():
        raw_isbn = row[mapping["isbn"]] if mapping.get("isbn") else None
        isbn = db.clean_isbn(raw_isbn)
        title = db.clean_text(row[mapping["title"]]) if mapping.get("title") else ""

        if not isbn or not title:
            reason = "ISBN 無法辨識" if not isbn else "缺少書名"
            skipped.append({"Excel 列號": idx + 1, "原始 ISBN": db.clean_text(raw_isbn),
                            "書名": title, "原因": reason})
            continue

        author = db.clean_text(row[mapping["author"]]) if mapping.get("author") else ""
        price = db.clean_price(row[mapping["price"]]) if mapping.get("price") else ""
        publisher = db.clean_text(row[mapping["publisher"]]) if mapping.get("publisher") else ""
        records[isbn] = {                      # 同一檔案內重複的 ISBN，以後出現的為準
            "isbn": isbn,
            "title": title,
            "author": author,
            "price": price,
            "publisher": publisher or fixed_publisher.strip(),
        }
    return list(records.values()), pd.DataFrame(skipped)
