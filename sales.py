"""
sales.py — 賣上分析的計算（不含畫面）。

輸入：賣上資料（條碼、商品名、賣上價格、賣上數量、合計賣上金額、庫存，欄位名稱可自動辨識）
對照：用條碼到商品資料庫（中文書 books.db、文具 stationery.db…）查商品名、店頭價格與分析用的欄位
分組：依選擇的「分析維度」分組——中文書可用類別（大類／小類）、出版社；
      文具可用棚番、出版社・メーカー、系列…（設定在 config.py 各種類的 "sales"），
      也可以用賣上資料本身的某一欄（例如報表裡的棚番欄）
輸出：分組彙總、（類別維度時）類別彙總、商品明細

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


def parse(df: pd.DataFrame, mapping: dict, extra_col: str | None = None):
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
                     "qty": qty, "amount": amount, "stock": stock,
                     "extra": db.clean_text(r[extra_col]) if extra_col else ""})
    return (pd.DataFrame(rows, columns=["code", "name", "unit_price", "qty", "amount", "stock", "extra"]),
            pd.DataFrame(skipped))


GROUP, SUB = "分組", "子分組"          # 明細裡的分析分組欄（顯示時換成維度名稱）
UNSET_GENERIC = "（未設定）"


def dimension_value(p, dim) -> str:
    """商品資料（一列）→ 這個分析維度的值；設了 fallback 的話，空白時改用 fallback 欄位。"""
    v = db.clean_text(p.get(dim["key"]))
    if not v and dim.get("fallback"):
        v = db.clean_text(p.get(dim["fallback"]))
    return v


def build_detail(rows: pd.DataFrame, products: pd.DataFrame, table: cats.Categories,
                 spec: dict | None = None, dim: dict | None = None) -> pd.DataFrame:
    """
    products：商品資料庫的整張表（db.fetch_all，欄名用英文 key）。
    spec：config 裡該種類的 "sales" 設定（商品名、明細欄位）；dim：選擇的分析維度。
    同一條碼、同一賣上價格的多筆合併成一列（不同折扣價分開列）。
    回傳的明細多三欄：分組、子分組、在資料庫（True/False）。
    """
    spec = spec or {"name": ["title"], "detail": [["類別", "category"], ["出版社", "publisher"]]}
    dim = dim or {"key": "category", "label": "類別", "mapped": True}
    from_sales = dim.get("source") == "sales"           # 維度來自賣上資料本身的欄位

    info = {}
    if not products.empty:
        pk = products.columns[0]
        for _, p in products.iterrows():
            info[p[pk]] = p
    # 庫存是「這本書現在有幾本」，同一本書出現好幾列時取最後一個有值的，不相加
    stock_of = rows.dropna(subset=["stock"]).groupby("code")["stock"].last().to_dict() \
        if "stock" in rows else {}
    agg = dict(name=("name", "first"), qty=("qty", "sum"), amount=("amount", "sum"))
    if "extra" in rows:
        agg["extra"] = ("extra", "first")
    g = (rows.groupby(["code", "unit_price"], dropna=False, sort=False).agg(**agg).reset_index())

    extra_cols = [label for label, _ in spec["detail"]]
    dim_label = dim["label"]
    show_dim_col = not dim.get("mapped") and dim_label not in extra_cols

    out = []
    for _, r in g.iterrows():
        p = info.get(r["code"])
        found = p is not None
        name = ""
        if found:
            name = next((db.clean_text(p.get(k)) for k in spec["name"] if db.clean_text(p.get(k))), "")
        rec = {"ISBN(JAN)": r["code"],
               "品番": db.clean_text(p.get("model")) if found else "",
               "商品名": name or r["name"]}
        if from_sales:
            value = db.clean_text(r.get("extra"))
        else:
            value = dimension_value(p, dim) if found else ""
        if show_dim_col:
            rec[dim_label] = value
        for label, key in spec["detail"]:
            rec[label] = db.clean_text(p.get(key)) if found else ""
        if from_sales and dim_label in extra_cols and not rec[dim_label]:
            rec[dim_label] = value                 # 例：賣上資料的棚番欄，資料庫還沒有棚番時直接顯示它
        rec.update({
            "店頭價格": to_number(p.get("price")) if found else None,
            "賣上價格": None if pd.isna(r["unit_price"]) else r["unit_price"],
            "賣上數量": r["qty"],
            "合計賣上金額": r["amount"],
            STOCK: stock_of.get(r["code"]),
        })
        if dim.get("mapped"):                      # 類別：依對照表歸到大類，子分組＝類別本身
            rec[GROUP] = table.major_of(value) if found else cats.NOT_FOUND
            rec[SUB] = value or "（空白）"
        else:
            rec[GROUP] = value or (UNSET_GENERIC if (found or from_sales) else cats.NOT_FOUND)
            rec[SUB] = ""
        rec["在資料庫"] = found
        out.append(rec)

    cols = ["ISBN(JAN)", "品番", "商品名"] + ([dim_label] if show_dim_col else []) + extra_cols \
        + ["店頭價格", "賣上價格", "賣上數量", "合計賣上金額", STOCK, GROUP, SUB, "在資料庫"]
    df = pd.DataFrame(out, columns=cols)
    for col in ("店頭價格", "賣上價格", STOCK):          # 沒有小數的話用整數顯示（空白保持空白）
        v = pd.to_numeric(df[col], errors="coerce")
        if (v.dropna() % 1 == 0).all():
            df[col] = v.astype("Int64")
    df.attrs["dim"] = dim
    return df.sort_values(["合計賣上金額", "賣上數量"], ascending=False).reset_index(drop=True)


def has_stock(detail: pd.DataFrame) -> bool:
    return STOCK in detail and detail[STOCK].notna().any()


def detail_cols(detail: pd.DataFrame) -> list[str]:
    """明細要顯示／下載的欄位：有庫存資料時，最後多一欄庫存。"""
    cols = [c for c in detail.columns if c not in (STOCK, GROUP, SUB, "在資料庫")]
    return cols + ([STOCK] if has_stock(detail) else [])


def group_labels(detail: pd.DataFrame) -> dict:
    """分組欄要顯示的名稱：類別維度是「大類／類別」，其他維度就是維度名稱。"""
    dim = detail.attrs.get("dim", {"mapped": True})
    if dim.get("mapped"):
        return {GROUP: "大類", SUB: "類別"}
    return {GROUP: dim.get("label", "分組")}


def _stock_by(detail: pd.DataFrame, keys: list[str]) -> pd.Series:
    """庫存合計：每本書只算一次（同一本書有不同賣上價格時不重複加）。"""
    d = detail.drop_duplicates("ISBN(JAN)")
    return d.groupby(keys)[STOCK].sum(min_count=1)


NEUTRAL_GROUPS = [cats.UNSET, cats.UNKNOWN, UNSET_GENERIC, cats.NOT_FOUND]


def group_order(detail: pd.DataFrame, table: cats.Categories) -> list[str]:
    """分組的排列順序：類別維度照對照表；其他維度照金額由大到小；未設定、查無放最後。"""
    present = set(detail[GROUP])
    dim = detail.attrs.get("dim", {"mapped": True})
    if dim.get("mapped"):
        head = [m for m in table.majors if m in present]
    else:
        amt = detail.groupby(GROUP)["合計賣上金額"].sum().sort_values(ascending=False)
        head = [m for m in amt.index if m not in NEUTRAL_GROUPS]
    return head + [m for m in NEUTRAL_GROUPS if m in present]


def major_order(table: cats.Categories) -> list[str]:
    return table.majors + [cats.UNSET, cats.UNKNOWN, cats.NOT_FOUND]


def pivot_major(detail: pd.DataFrame, table: cats.Categories) -> pd.DataFrame:
    """分組彙總：品項數、冊數、金額、占比，最後一列是合計。欄名是內部名稱「分組」。"""
    g = detail.groupby(GROUP).agg(品項數=("ISBN(JAN)", "nunique"), 冊數=("賣上數量", "sum"),
                                  小計金額=("合計賣上金額", "sum"))
    g = g.loc[[m for m in group_order(detail, table) if m in g.index]].reset_index()
    tq, ta = g["冊數"].sum(), g["小計金額"].sum()
    g["冊數占比"] = g["冊數"] / tq if tq else 0
    g["金額占比"] = g["小計金額"] / ta if ta else 0
    total = {GROUP: "合計", "品項數": detail["ISBN(JAN)"].nunique(), "冊數": tq,
             "小計金額": ta, "冊數占比": 1.0 if tq else 0, "金額占比": 1.0 if ta else 0}
    if has_stock(detail):
        g["庫存冊數"] = g[GROUP].map(_stock_by(detail, [GROUP])).astype("Int64")
        total["庫存冊數"] = int(g["庫存冊數"].sum())
    return pd.concat([g, pd.DataFrame([total])], ignore_index=True)


def pivot_minor(detail: pd.DataFrame, table: cats.Categories) -> pd.DataFrame | None:
    """子分組彙總（只有類別維度有：大類底下的類別），每個大類後面有小計列。"""
    if not detail.attrs.get("dim", {"mapped": True}).get("mapped"):
        return None
    g = detail.groupby([GROUP, SUB]).agg(品項數=("ISBN(JAN)", "nunique"), 冊數=("賣上數量", "sum"),
                                         小計金額=("合計賣上金額", "sum")).reset_index()
    stock = has_stock(detail)
    if stock:
        sb = _stock_by(detail, [GROUP, SUB])
        g["庫存冊數"] = [sb.get((a, b)) for a, b in zip(g[GROUP], g[SUB])]
        g["庫存冊數"] = g["庫存冊數"].astype("Int64")
    out = []
    for m in group_order(detail, table):
        part = g[g[GROUP] == m].sort_values("小計金額", ascending=False)
        if part.empty:
            continue
        out.append(part)
        sub = {GROUP: m, SUB: "小計", "品項數": part["品項數"].sum(),
               "冊數": part["冊數"].sum(), "小計金額": part["小計金額"].sum()}
        if stock:
            sub["庫存冊數"] = int(part["庫存冊數"].sum())
        out.append(pd.DataFrame([sub]))
    return pd.concat(out, ignore_index=True) if out else g


def count_labels(unit: str) -> dict:
    """中文書用「冊」，其他商品用「數量」。"""
    if unit == "冊":
        return {}
    return {"冊數": "數量", "冊數占比": "數量占比", "庫存冊數": "庫存數"}


def to_excel_bytes(major: pd.DataFrame, minor: pd.DataFrame | None, detail: pd.DataFrame,
                   unit: str = "冊") -> bytes:
    import io
    names = dict(group_labels(detail), **count_labels(unit))
    top = names[GROUP]
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        major.rename(columns=names).to_excel(w, sheet_name=f"{top}彙總"[:31], index=False)
        if minor is not None:
            minor.rename(columns=names).to_excel(w, sheet_name="類別彙總", index=False)
        cols = detail_cols(detail) + [GROUP] + ([SUB] if group_labels(detail).get(SUB) else [])
        out = detail[cols].rename(columns=names)
        out = out.loc[:, ~out.columns.duplicated()]
        out.to_excel(w, sheet_name="商品明細", index=False)
    return buf.getvalue()
