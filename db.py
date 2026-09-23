"""
db.py — 資料庫（SQLite）的所有操作。

每個商品種類一個 .db 檔（設定在 config.py），表格結構由設定自動產生：
    <主鍵欄>    ISBN 或 JAN（同一個碼只會有一筆）
    <其他欄>    書名、作者、價格……
    search_key  把可搜尋的欄位正規化後串起來，專門給關鍵字查詢用
    updated_at  最後更新時間
"""
import re
import sqlite3
import unicodedata
from datetime import datetime

import pandas as pd


# ---------------------------------------------------------------- 文字處理
def normalize(text) -> str:
    """全形轉半形、英文轉小寫、去頭尾空白。查詢和儲存都用同一套規則，才比得起來。"""
    if text is None:
        return ""
    s = str(text)
    if s.lower() in ("nan", "none"):
        return ""
    return unicodedata.normalize("NFKC", s).lower().strip()


def clean_text(text) -> str:
    if text is None:
        return ""
    s = str(text).strip()
    return "" if s.lower() in ("nan", "none") else s


def _digits(raw) -> str:
    s = normalize(raw).upper()
    if s.endswith(".0"):          # Excel 把數字讀成小數時會多出 .0
        s = s[:-2]
    return re.sub(r"[^0-9X]", "", s)


def _isbn10_to_13(isbn10: str) -> str:
    core = "978" + isbn10[:9]
    total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(core))
    return core + str((10 - total % 10) % 10)


def clean_isbn(raw) -> str | None:
    s = _digits(raw)
    if len(s) == 13 and s.isdigit():
        return s
    if len(s) == 10 and s[:9].isdigit():
        return _isbn10_to_13(s)
    return None


def clean_jan(raw) -> str | None:
    """JAN / EAN：13 碼或 8 碼。12 碼（開頭的 0 被 Excel 吃掉）自動補回。"""
    s = _digits(raw)
    if not s.isdigit():
        return None
    if len(s) == 12:
        s = "0" + s
    return s if len(s) in (8, 13) else None


def clean_price(raw) -> str:
    """「NT$1,200元」「110.0」之類的寫法統一成「1200」「110」；無法辨識就原樣保留。"""
    s = clean_text(raw)
    if not s:
        return ""
    t = re.sub(r"(nt\$|nt|\$|¥|円|元|,|\s)", "", normalize(s))
    try:
        v = float(t)
        return str(int(v)) if v == int(v) else str(v)
    except ValueError:
        return s


CLEANERS = {"isbn": clean_isbn, "jan": clean_jan, "price": clean_price, "text": clean_text}


# ---------------------------------------------------------------- 設定小工具
def key_field(cat) -> str:
    return cat["fields"][0]["key"]


def labels(cat) -> list[str]:
    return [f["label"] for f in cat["fields"]]


def _search_fields(cat) -> list[str]:
    return [f["key"] for f in cat["fields"] if f.get("search", True)]


def _make_search_key(values: dict, cat) -> str:
    return " ".join(normalize(values.get(k, "")) for k in _search_fields(cat))


# ---------------------------------------------------------------- 連線與建表
def connect(path: str, cat) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    t, key = cat["table"], key_field(cat)
    others = ",\n".join(f"    {f['key']} TEXT NOT NULL DEFAULT ''" for f in cat["fields"][1:])
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {t} (
            {key} TEXT PRIMARY KEY,
        {others},
            search_key TEXT NOT NULL DEFAULT '',
            updated_at TEXT
        )""")
    # 設定裡新增了欄位、但舊的 .db 還沒有：自動補上
    existing = {r[1] for r in conn.execute(f"PRAGMA table_info({t})")}
    for f in cat["fields"]:
        if f["key"] not in existing:
            conn.execute(f"ALTER TABLE {t} ADD COLUMN {f['key']} TEXT NOT NULL DEFAULT ''")
    conn.commit()
    return conn


def count(conn, cat) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {cat['table']}").fetchone()[0]


def rebuild_search_keys(conn, cat) -> None:
    """重算搜尋欄位。網站啟動時執行一次，用 DB Browser 手動改過的資料也查得到。"""
    keys = [f["key"] for f in cat["fields"]]
    rows = conn.execute(f"SELECT {', '.join(keys)} FROM {cat['table']}").fetchall()
    conn.executemany(
        f"UPDATE {cat['table']} SET search_key = ? WHERE {keys[0]} = ?",
        [(_make_search_key(dict(zip(keys, r)), cat), r[0]) for r in rows],
    )
    conn.commit()


# ---------------------------------------------------------------- 查詢
def _natural_key(text: str):
    """讓「航海王 2」排在「航海王 10」前面；有無空格（航海王105／航海王 105）一視同仁。"""
    text = normalize(text).replace(" ", "")
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", text)]


def _prepare_tokens(query: str) -> list[str]:
    tokens = []
    for t in normalize(query).split():
        if re.fullmatch(r"[0-9\-x]+", t):   # 看起來像條碼：拿掉連字號
            t = t.replace("-", "")
        if t:
            tokens.append(t)
    return tokens


def search(conn, cat, query: str, limit: int = 300) -> tuple[pd.DataFrame, bool]:
    """每個關鍵字都必須出現（AND），但可以在任何一欄。回傳 (結果表, 是否被截斷)"""
    keys = [f["key"] for f in cat["fields"]]
    tokens = _prepare_tokens(query)
    if not tokens:
        return pd.DataFrame(columns=labels(cat)), False

    where, params = [], []
    for t in tokens:
        t = t.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        where.append("search_key LIKE ? ESCAPE '\\'")
        params.append(f"%{t}%")
    sql = f"SELECT {', '.join(keys)} FROM {cat['table']} WHERE {' AND '.join(where)} LIMIT ?"
    params.append(limit + 1)
    rows = conn.execute(sql, params).fetchall()

    truncated = len(rows) > limit
    rows = rows[:limit]
    rows.sort(key=lambda r: (_natural_key(r[1]), r[0]))
    return pd.DataFrame(rows, columns=labels(cat)), truncated


# ---------------------------------------------------------------- 寫入
def upsert(conn, cat, records: list[dict]) -> tuple[int, int]:
    """
    已存在的碼會更新；新檔案裡某欄空白時，保留資料庫原本的值。
    回傳 (新增筆數, 更新筆數)
    """
    if not records:
        return 0, 0
    t, keys = cat["table"], [f["key"] for f in cat["fields"]]
    pk = keys[0]
    ids = list({r[pk] for r in records})

    existing = set()
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        q = f"SELECT {pk} FROM {t} WHERE {pk} IN ({','.join('?' * len(chunk))})"
        existing.update(row[0] for row in conn.execute(q, chunk))

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sets = ",\n".join(f"{k} = COALESCE(NULLIF(excluded.{k}, ''), {t}.{k})" for k in keys[1:])
    sql = f"""
        INSERT INTO {t} ({', '.join(keys)}, search_key, updated_at)
        VALUES ({', '.join('?' * len(keys))}, '', ?)
        ON CONFLICT({pk}) DO UPDATE SET
        {sets},
        updated_at = excluded.updated_at"""
    conn.executemany(sql, [tuple(r.get(k, "") for k in keys) + (now,) for r in records])

    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        q = f"SELECT {', '.join(keys)} FROM {t} WHERE {pk} IN ({','.join('?' * len(chunk))})"
        updates = [(_make_search_key(dict(zip(keys, r)), cat), r[0])
                   for r in conn.execute(q, chunk).fetchall()]
        conn.executemany(f"UPDATE {t} SET search_key = ? WHERE {pk} = ?", updates)

    conn.commit()
    inserted = sum(1 for i in ids if i not in existing)
    return inserted, len(ids) - inserted
