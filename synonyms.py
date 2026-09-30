"""
synonyms.py — 同義詞表：指同一個東西的不同寫法（不同語言、簡稱、舊名……）。

synonyms.csv 每一列是一組同義詞，名稱1、名稱2……往右填，用不到的格子留空。
例：排球少年 | ハイキュー | Haikyu
    百樂     | PILOT      | パイロット

查詢時，關鍵字只要跟表中任何一個名稱「完全相同」（不分全形半形、大小寫），
就會同時用同一列的所有名稱去找。所有商品種類共用這張表。
"""
import io
import os

import pandas as pd

import db

FILENAME = "synonyms.csv"
DEFAULT_COLUMNS = [f"名稱{i}" for i in range(1, 7)]


def read_table(path: str) -> pd.DataFrame:
    """讀成表格（給編輯畫面用）。檔案不存在時回傳空表。"""
    if not os.path.exists(path):
        return pd.DataFrame(columns=DEFAULT_COLUMNS)
    df = pd.read_csv(path, dtype=str, encoding="utf-8-sig").fillna("")
    return df if len(df.columns) else pd.DataFrame(columns=DEFAULT_COLUMNS)


def to_csv_bytes(df: pd.DataFrame) -> bytes:
    """存檔用：去掉整列空白的列，用 Excel 開也不會亂碼的 UTF-8（含 BOM）。"""
    df = df.fillna("").astype(str).apply(lambda col: col.str.strip())
    df = df[(df != "").any(axis=1)]
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8-sig")


def build_lookup(df: pd.DataFrame) -> dict[str, list[str]]:
    """{正規化後的名稱: [同組所有名稱（正規化）]}；同一名稱出現在多組時合併。"""
    lookup: dict[str, set[str]] = {}
    for _, row in df.iterrows():
        group = {db.normalize(v) for v in row.tolist() if db.normalize(v)}
        if len(group) < 2:
            continue
        for name in group:
            lookup.setdefault(name, set()).update(group)
    return {k: sorted(v) for k, v in lookup.items()}
