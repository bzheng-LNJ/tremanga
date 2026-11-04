"""
sales.py — 賣上分析的計算（不含畫面）。

輸入：賣上資料（條碼、商品名、賣上價格、賣上數量、合計賣上金額，欄位名稱可自動辨識）
對照：用條碼到商品資料庫（books.db）查商品名、類別、出版社、店頭價格（建檔時的定價）
輸出：商品明細、類別（小類）彙總、大類彙總

賣上資料只在當下計算，不會存進資料庫或 GitHub。
"""
import re

import pandas as pd

import categories as cats
import db

# 賣上資料的欄位（給 importer 的自動辨識用，格式跟 config 的 fields 相同）
SALES_SPEC = {
    "fields": [
        {"key": "code", "label": "ISBN(JAN)", "kind": "jan",
         "aliases": ["isbn(jan)", "isbn", "jan", "janコード", "條碼", "商品條碼", "國際條碼", "書號",
                     "商品コード", "barcode", "ean"]},
        {"key": "name", "label": "商品名", "kind": "text",
         "aliases": ["商品名", "商品名稱", "書名", "品名"]},
        {"key": "unit_price", "label": "賣上價格", "kind": "price",
         "aliases": ["賣上價格", "売上価格", "賣價", "售價", "實售價", "實際售價", "單價", "販売価格", "実売価格", "売価"]},
        {"key": "qty", "label": "賣上數量", "kind": "text",
         "aliases": ["賣上數量", "売上数量", "數量", "数量", "冊數", "銷售數量", "販売数", "売上数", "売数"]},
        {"key": "amount", "label": "合計賣上金額", "kind": "price",
         "aliases": ["合計賣上金額", "賣上金額", "売上金額", "合計金額", "銷售金額", "小計", "金額", "売上合計"]},
        {"key": "stock", "label": "庫存（選用）", "kind": "text",
         "aliases": ["庫存", "庫存量", "庫存數", "庫存數量", "在庫", "在庫数", "在庫数量", "現在庫", "現在庫数",
                     "店在庫", "stock"]},
    ],
}
# 條碼一定要有；賣上數量、庫存至少要有一個；有賣上數量時，賣上價格、合計金額至少要有一個
REQUIRED = ["code"]
STOCK = "庫存"

DETAIL_COLS = ["ISBN(JAN)", "品番", "商品名", "類別", "出版社", "店頭價格", "賣上價格", "賣上數量", "合計賣上金額"]


def to_number(raw):
    """「1,200」「NT$350」「-2」「350.0」→ 數字；空白或無法辨識 → None。"""
    s = db.clean_text(raw)
    if not s:
        return None
    s = re.sub(r"(nt\$|nt|\$|¥|円|元|,|\s)", "", db.normalize(s))
    neg = s.startswith("(") and s.endswith(")")        # 會計格式 (100) = -100
    s = s.strip("()")
    try:
        v = float(s)
    except ValueError:
        return None
    v = -v if neg else v
    return int(v) if v == int(v) else v


def clean_code(raw) -> str | None:
    return db.clean_jan(raw) or db.clean_isbn(raw)


def parse(df: pd.DataFrame, mapping: dict):
    """
    依欄位對應整理賣上資料。
    回傳 (rows DataFrame[code, name, unit_price, qty, amount, stock], 略過的列 DataFrame)
    只有庫存、沒有賣上的列（例如庫存清單）會當作賣上 0 冊、0 元，方便一起查補類別。
    """
    rows, skipped = [], []
    for idx, r in df.iterrows():
        def get(k):
            return r[mapping[k]] if mapping.get(k) else None

        code = clean_code(get("code"))
        qty = to_number(get("qty"))
        price = to_number(get("unit_price"))
        amount = to_number(get("amount"))
        stock = to_number(get("stock"))
        if not code:
            skipped.append({"Excel 列號": idx + 1, "原始條碼": db.clean_text(get("code")), "原因": "條碼無法辨識（合計列也會被略過）"})
            continue
        if qty is None:
            if stock is None:
                skipped.append({"Excel 列號": idx + 1, "原始條碼": code, "原因": "沒有數量也沒有庫存"})
                continue
            qty = 0
        if amount is None and qty == 0:
            amount = 0
        if amount is None and price is not None:
            amount = price * qty
        if price is None and amount is not None and qty:
            price = round(amount / qty, 2)
            price = int(price) if price == int(price) else price
        if amount is None:
            skipped.append({"Excel 列號": idx + 1, "原始條碼": code, "原因": "沒有賣上價格也沒有金額"})
            continue
        rows.append({"code": code, "name": db.clean_text(get("name")), "unit_price": price,
                     "qty": qty, "amount": amount, "stock": stock})
    return (pd.DataFrame(rows, columns=["code", "name", "unit_price", "qty", "amount", "stock"]),
            pd.DataFrame(skipped))


def build_detail(rows: pd.DataFrame, products: pd.DataFrame, table: cats.Categories) -> pd.DataFrame:
    """
    products：商品資料庫的整張表（db.fetch_all，欄名用英文 key）。
    同一條碼、同一賣上價格的多筆合併成一列（不同折扣價分開列）。
    回傳的明細多兩欄：大類、在資料庫（True/False）。
    """
    info = {}
    if not products.empty:
        pk = products.columns[0]
        for _, p in products.iterrows():
            info[p[pk]] = p
    # 庫存是「這本書現在有幾本」，同一本書出現好幾列時取最後一個有值的，不相加
    stock_of = rows.dropna(subset=["stock"]).groupby("code")["stock"].last().to_dict() \
        if "stock" in rows else {}
    g = (rows.groupby(["code", "unit_price"], dropna=False, sort=False)
             .agg(name=("name", "first"), qty=("qty", "sum"), amount=("amount", "sum"))
             .reset_index())
    out = []
    for _, r in g.iterrows():
        p = info.get(r["code"])
        found = p is not None
        title = (p.get("title") or p.get("name") or "") if found else ""
        category = (p.get("category") or "") if found else ""
        shelf = to_number(p.get("price")) if found else None
        out.append({
            "ISBN(JAN)": r["code"],
            "品番": (p.get("model") or "") if found else "",
            "商品名": title or r["name"],
            "類別": category,
            "出版社": (p.get("publisher") or "") if found else "",
            "店頭價格": shelf,
            "賣上價格": None if pd.isna(r["unit_price"]) else r["unit_price"],
            "賣上數量": r["qty"],
            "合計賣上金額": r["amount"],
            STOCK: stock_of.get(r["code"]),
            "大類": table.major_of(category) if found else cats.NOT_FOUND,
            "在資料庫": found,
        })
    df = pd.DataFrame(out, columns=DETAIL_COLS + [STOCK, "大類", "在資料庫"])
    for col in ("店頭價格", "賣上價格", STOCK):          # 沒有小數的話用整數顯示（空白保持空白）
        v = pd.to_numeric(df[col], errors="coerce")
        if (v.dropna() % 1 == 0).all():
            df[col] = v.astype("Int64")
    return df.sort_values(["合計賣上金額", "賣上數量"], ascending=False).reset_index(drop=True)


def has_stock(detail: pd.DataFrame) -> bool:
    return STOCK in detail and detail[STOCK].notna().any()


def detail_cols(detail: pd.DataFrame) -> list[str]:
    """明細要顯示／下載的欄位：有庫存資料時，最後多一欄庫存。"""
    return DETAIL_COLS + ([STOCK] if has_stock(detail) else [])


def _stock_by(detail: pd.DataFrame, keys: list[str]) -> pd.Series:
    """庫存合計：每本書只算一次（同一本書有不同賣上價格時不重複加）。"""
    d = detail.drop_duplicates("ISBN(JAN)")
    return d.groupby(keys)[STOCK].sum(min_count=1)


def major_order(table: cats.Categories) -> list[str]:
    return table.majors + [cats.UNSET, cats.UNKNOWN, cats.NOT_FOUND]


def pivot_major(detail: pd.DataFrame, table: cats.Categories) -> pd.DataFrame:
    """大類彙總：品項數、冊數、金額、占比（依對照表順序），最後一列是合計。"""
    g = detail.groupby("大類").agg(品項數=("ISBN(JAN)", "nunique"), 冊數=("賣上數量", "sum"),
                                   小計金額=("合計賣上金額", "sum"))
    order = [m for m in major_order(table) if m in g.index]
    g = g.loc[order].reset_index()
    tq, ta = g["冊數"].sum(), g["小計金額"].sum()
    g["冊數占比"] = g["冊數"] / tq if tq else 0
    g["金額占比"] = g["小計金額"] / ta if ta else 0
    total = {"大類": "合計", "品項數": detail["ISBN(JAN)"].nunique(), "冊數": tq,
             "小計金額": ta, "冊數占比": 1.0 if tq else 0, "金額占比": 1.0 if ta else 0}
    if has_stock(detail):
        g["庫存冊數"] = g["大類"].map(_stock_by(detail, ["大類"])).astype("Int64")
        total["庫存冊數"] = int(g["庫存冊數"].sum())
    return pd.concat([g, pd.DataFrame([total])], ignore_index=True)


def pivot_minor(detail: pd.DataFrame, table: cats.Categories) -> pd.DataFrame:
    """類別（小類）彙總，依大類排列，每個大類後面有小計列。"""
    d = detail.assign(類別=detail["類別"].replace("", "（空白）"))
    g = d.groupby(["大類", "類別"]).agg(品項數=("ISBN(JAN)", "nunique"), 冊數=("賣上數量", "sum"),
                                        小計金額=("合計賣上金額", "sum")).reset_index()
    stock = has_stock(detail)
    if stock:
        sb = _stock_by(d, ["大類", "類別"])
        g["庫存冊數"] = [sb.get((a, b)) for a, b in zip(g["大類"], g["類別"])]
        g["庫存冊數"] = g["庫存冊數"].astype("Int64")
    out = []
    for m in major_order(table):
        part = g[g["大類"] == m].sort_values("小計金額", ascending=False)
        if part.empty:
            continue
        out.append(part)
        sub = {"大類": m, "類別": "小計", "品項數": part["品項數"].sum(),
               "冊數": part["冊數"].sum(), "小計金額": part["小計金額"].sum()}
        if stock:
            sub["庫存冊數"] = int(part["庫存冊數"].sum())
        out.append(pd.DataFrame([sub]))
    return pd.concat(out, ignore_index=True) if out else g


def to_excel_bytes(major: pd.DataFrame, minor: pd.DataFrame, detail: pd.DataFrame) -> bytes:
    import io
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        major.to_excel(w, sheet_name="大類彙總", index=False)
        minor.to_excel(w, sheet_name="類別彙總", index=False)
        detail[detail_cols(detail) + ["大類"]].to_excel(w, sheet_name="商品明細", index=False)
    return buf.getvalue()
