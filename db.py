"""
db.py — 書目資料庫（SQLite）的所有操作都集中在這裡。

資料表 books 只有一張：
    isbn        ISBN-13（主鍵，同一本書只會有一筆）
    title       書名
    author      作者
    publisher   出版社
    search_key  把上面四欄正規化後串起來，專門給關鍵字查詢用
    updated_at  最後更新時間
"""
import re
import sqlite3
import unicodedata
from datetime import datetime

import pandas as pd

SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    isbn       TEXT PRIMARY KEY,
    title      TEXT NOT NULL,
    author     TEXT NOT NULL DEFAULT '',
    publisher  TEXT NOT NULL DEFAULT '',
    search_key TEXT NOT NULL DEFAULT '',
    updated_at TEXT
);
"""


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
    """給顯示用的文字：只去掉頭尾空白和空值，不改大小寫。"""
    if text is None:
        return ""
    s = str(text).strip()
    return "" if s.lower() in ("nan", "none") else s


def _isbn10_to_13(isbn10: str) -> str:
    core = "978" + isbn10[:9]
    total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(core))
    return core + str((10 - total % 10) % 10)


def clean_isbn(raw) -> str | None:
    """把各種寫法的 ISBN 統一成 13 碼純數字；無法辨識就回傳 None。"""
    if raw is None:
        return None
    s = normalize(raw).upper()
    if s.endswith(".0"):          # Excel 把數字讀成小數時會多出 .0
        s = s[:-2]
    s = re.sub(r"[^0-9X]", "", s)  # 去掉連字號、空白等
    if len(s) == 13 and s.isdigit():
        return s
    if len(s) == 10 and s[:9].isdigit():
        return _isbn10_to_13(s)
    return None


def make_search_key(isbn, title, author, publisher) -> str:
    return " ".join(normalize(x) for x in (isbn, title, author, publisher))


# ---------------------------------------------------------------- 連線與建表
def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute(SCHEMA)
    conn.commit()
    return conn


def count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM books").fetchone()[0]


# ---------------------------------------------------------------- 查詢
def _natural_key(text: str):
    """讓「航海王 2」排在「航海王 10」前面，而不是照字元順序。"""
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", text)]


def _prepare_tokens(query: str) -> list[str]:
    tokens = []
    for t in normalize(query).split():
        if re.fullmatch(r"[0-9\-x]+", t):   # 看起來像 ISBN：把連字號拿掉
            t = t.replace("-", "")
        if t:
            tokens.append(t)
    return tokens


def search(conn, query: str, limit: int = 300) -> tuple[pd.DataFrame, bool]:
    """
    用空白分隔的每個關鍵字都必須出現（AND），但可以出現在任何一欄。
    例：「航海王 105」＝書名含航海王、且某欄含 105。
    回傳 (結果表, 是否因超過上限而被截斷)
    """
    tokens = _prepare_tokens(query)
    if not tokens:
        return pd.DataFrame(columns=["ISBN", "書名", "作者", "出版社"]), False

    where, params = [], []
    for t in tokens:
        t = t.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        where.append("search_key LIKE ? ESCAPE '\\'")
        params.append(f"%{t}%")

    sql = (
        "SELECT isbn, title, author, publisher FROM books WHERE "
        + " AND ".join(where)
        + " LIMIT ?"
    )
    params.append(limit + 1)
    rows = conn.execute(sql, params).fetchall()

    truncated = len(rows) > limit
    rows = rows[:limit]
    rows.sort(key=lambda r: (_natural_key(r[1]), r[0]))
    df = pd.DataFrame(rows, columns=["ISBN", "書名", "作者", "出版社"])
    return df, truncated


# ---------------------------------------------------------------- 寫入
def upsert(conn, records: list[dict]) -> tuple[int, int]:
    """
    records 每筆需有 isbn, title，可有 author, publisher。
    已存在的 ISBN 會更新；新檔案裡某欄是空白時，保留資料庫原本的值。
    回傳 (新增筆數, 更新筆數)
    """
    if not records:
        return 0, 0

    existing = set()
    isbns = [r["isbn"] for r in records]
    for i in range(0, len(isbns), 500):
        chunk = isbns[i:i + 500]
        q = f"SELECT isbn FROM books WHERE isbn IN ({','.join('?' * len(chunk))})"
        existing.update(row[0] for row in conn.execute(q, chunk))

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sql = """
    INSERT INTO books (isbn, title, author, publisher, search_key, updated_at)
    VALUES (?, ?, ?, ?, '', ?)
    ON CONFLICT(isbn) DO UPDATE SET
        title      = COALESCE(NULLIF(excluded.title, ''),     books.title),
        author     = COALESCE(NULLIF(excluded.author, ''),    books.author),
        publisher  = COALESCE(NULLIF(excluded.publisher, ''), books.publisher),
        updated_at = excluded.updated_at
    """
    conn.executemany(sql, [
        (r["isbn"], r.get("title", ""), r.get("author", ""), r.get("publisher", ""), now)
        for r in records
    ])

    # 以合併後的最終內容重算搜尋欄位
    for i in range(0, len(isbns), 500):
        chunk = isbns[i:i + 500]
        q = f"SELECT isbn, title, author, publisher FROM books WHERE isbn IN ({','.join('?' * len(chunk))})"
        updates = [(make_search_key(*row), row[0]) for row in conn.execute(q, chunk).fetchall()]
        conn.executemany("UPDATE books SET search_key = ? WHERE isbn = ?", updates)

    conn.commit()
    inserted = sum(1 for i in set(isbns) if i not in existing)
    updated = len(set(isbns)) - inserted
    return inserted, updated


def rebuild_search_keys(conn) -> None:
    """重算所有書的搜尋欄位。網站啟動時執行一次，這樣用 DB Browser 手動改過的資料也查得到。"""
    rows = conn.execute("SELECT isbn, title, author, publisher FROM books").fetchall()
    conn.executemany("UPDATE books SET search_key = ? WHERE isbn = ?",
                     [(make_search_key(*r), r[0]) for r in rows])
    conn.commit()
