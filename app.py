"""
app.py — 中文書籍查詢系統（Streamlit）

  🔍 商品查詢：選商品種類 → 輸入關鍵字 → 在表格上選取範圍複製
  🛠 資料維護：選商品種類 → 上傳建檔檔案 → 併入 → 下載該種類的 .db → 上傳到 GitHub

商品種類與欄位設定在 config.py。每個種類各自一個 .db 檔，查詢時不會混在一起。
"""
import os
import tempfile

import streamlit as st

import db
import importer
from config import CATEGORIES

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MAX_RESULTS = 300
CAT_IDS = list(CATEGORIES)

st.set_page_config(page_title="中文書籍查詢系統", page_icon="📚", layout="wide")


def db_path(cat_id: str) -> str:
    return os.path.join(BASE_DIR, CATEGORIES[cat_id]["db"])


@st.cache_resource
def get_conn(cat_id: str, _mtime):
    # _mtime 讓 .db 被換掉（重新部署）時自動重開連線
    cat = CATEGORIES[cat_id]
    conn = db.connect(db_path(cat_id), cat)
    db.rebuild_search_keys(conn, cat)
    return conn


def current_conn(cat_id: str):
    p = db_path(cat_id)
    return get_conn(cat_id, os.path.getmtime(p) if os.path.exists(p) else 0)


def pick_category(key: str) -> str:
    return st.radio("商品種類", CAT_IDS, format_func=lambda c: CATEGORIES[c]["label"],
                    horizontal=True, key=key)


# ====================================================================== 查詢頁
def page_search():
    st.title("📚 中文書籍查詢系統")
    cat_id = pick_category("search_cat")
    cat = CATEGORIES[cat_id]
    conn = current_conn(cat_id)

    total = db.count(conn, cat)
    st.caption(f"{cat['label']}：目前收錄 {total:,} 筆")
    if total == 0:
        st.info(f"{cat['label']}還沒有資料，請先到「資料維護」匯入。")
        return

    q = st.text_input("關鍵字", placeholder=cat["placeholder"], key=f"q_{cat_id}")
    if not q.strip():
        st.info("輸入關鍵字後按 Enter 查詢。")
        return

    df, truncated = db.search(conn, cat, q, limit=MAX_RESULTS)
    if df.empty:
        st.warning("查無資料。可以試試少打幾個字，或只用名稱的一部分查詢。")
        return
    if truncated:
        st.warning(f"結果超過 {MAX_RESULTS} 筆，只顯示前 {MAX_RESULTS} 筆，請加上更多關鍵字縮小範圍。")
    else:
        st.success(f"找到 {len(df)} 筆")

    widths = {f["label"]: ("large" if i == 1 else "small" if f["kind"] == "price" else "medium")
              for i, f in enumerate(cat["fields"])}
    event = st.dataframe(
        df,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key=f"results_{cat_id}",
        column_config={k: st.column_config.TextColumn(width=w) for k, w in widths.items()},
    )

    st.subheader("複製單筆")
    selected = event.selection.rows
    if selected:
        row = df.iloc[selected[0]]
        ratios = [{"large": 2, "medium": 1.2, "small": 0.6}[widths[c]] for c in df.columns]
        for col, field in zip(st.columns(ratios), df.columns):
            with col:
                st.caption(field)
                st.code(row[field] or "（無資料）", language=None)
        st.caption("按每格右上角的圖示即可複製該欄。")
    else:
        st.caption("點選表格最左邊的方格選取一筆，這裡會出現它的各欄資料，可以單獨複製。"
                   "也可以直接在表格上拖曳選取範圍，按 Ctrl+C（Mac 為 ⌘+C）複製。")


# ====================================================================== 維護頁
def _load_db_bytes(cat_id: str) -> bytes:
    p = db_path(cat_id)
    if os.path.exists(p):
        with open(p, "rb") as f:
            return f.read()
    with tempfile.TemporaryDirectory() as d:     # 還沒有這個 .db：建一個空的
        tmp = os.path.join(d, "x.db")
        db.connect(tmp, CATEGORIES[cat_id]).close()
        with open(tmp, "rb") as f:
            return f.read()


def _merge_into(db_bytes: bytes, cat, records: list[dict]):
    """把 records 併進 db 的複本，回傳 (新的 db bytes, 新增數, 更新數, 總筆數)"""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "x.db")
        with open(p, "wb") as f:
            f.write(db_bytes)
        conn = db.connect(p, cat)
        inserted, updated = db.upsert(conn, cat, records)
        total = db.count(conn, cat)
        conn.execute("VACUUM")
        conn.close()
        with open(p, "rb") as f:
            return f.read(), inserted, updated, total


def page_maintain():
    st.title("🛠 資料維護")
    cat_id = pick_category("maintain_cat")
    cat = CATEGORIES[cat_id]
    fname = cat["db"]

    st.markdown(
        f"1. 上傳{cat['label']}的建檔檔案（Excel 或 CSV），確認欄位對應後按「併入資料庫」。可以連續處理好幾個檔案。\n"
        f"2. 全部處理完，按「下載 {fname}」。\n"
        f"3. 到 GitHub 儲存庫**最外層**上傳這個 {fname}，覆蓋舊檔。網站會自動更新。"
    )
    st.info("在這頁的操作不會直接改到線上資料，要等步驟 3 上傳後才會生效。")

    ss = st.session_state
    ss.setdefault("work", {})
    if cat_id not in ss.work:
        ss.work[cat_id] = {"db": _load_db_bytes(cat_id), "log": []}
    work = ss.work[cat_id]

    up = st.file_uploader("上傳建檔檔案", type=["xlsx", "xls", "csv"], key=f"up_{cat_id}")
    if up is not None:
        data = up.getvalue()
        try:
            sheets = importer.sheet_names(data, up.name)
            sheet = st.selectbox("工作表", sheets) if len(sheets) > 1 else None
            raw = importer.read_raw(data, up.name, sheet)
        except Exception as e:
            st.error(f"讀不了這個檔案：{e}")
            return

        guess = importer.detect_header_row(raw, cat)
        header_row = st.number_input(
            "表頭在第幾列（欄位名稱那一列）", min_value=1, max_value=max(len(raw), 1), value=guess + 1
        ) - 1
        df = importer.apply_header(raw, header_row)

        st.markdown("**欄位對應**（系統先自動猜，不對的話用下拉選單改）")
        auto = importer.guess_mapping(list(df.columns), cat)
        options = ["（不使用）"] + list(df.columns)
        mapping = {}
        for col, f in zip(st.columns(len(cat["fields"])), cat["fields"]):
            with col:
                default = options.index(auto[f["key"]]) if auto[f["key"]] else 0
                choice = st.selectbox(f["label"], options, index=default,
                                      key=f"map_{cat_id}_{f['key']}_{up.name}")
                mapping[f["key"]] = None if choice == "（不使用）" else choice

        for a in cat.get("append", []):
            into = next(f["label"] for f in cat["fields"] if f["key"] == a["into"])
            default = options.index(auto[a["key"]]) if auto[a["key"]] else 0
            choice = st.selectbox(
                f"{a['label']}（檔案裡{a['label']}另外一欄時才指定，會接在{into}後面）",
                options, index=default, key=f"map_{cat_id}_{a['key']}_{up.name}")
            mapping[a["key"]] = None if choice == "（不使用）" else choice

        fill_values = {}
        for f in cat["fields"]:
            if f["key"] in cat["fill_in"] and not mapping[f["key"]]:
                fill_values[f["key"]] = st.text_input(
                    f"這個檔案沒有{f['label']}欄位，請填{f['label']}（會套用到全部商品）",
                    key=f"fill_{cat_id}_{f['key']}")

        labels = {f["key"]: f["label"] for f in cat["fields"]}
        lacking = [labels[k] for k in cat["required"] if not mapping[k]]
        if lacking:
            st.warning(f"{'、'.join(lacking)} 一定要指定。")
            return

        records, skipped = importer.build_records(df, mapping, cat, fill_values)
        st.markdown(f"**預覽**：可匯入 {len(records)} 筆，略過 {len(skipped)} 筆")
        if records:
            st.dataframe([{labels[k]: v for k, v in r.items()} for r in records[:20]], hide_index=True)
        if len(skipped):
            with st.expander(f"查看略過的 {len(skipped)} 筆"):
                st.dataframe(skipped, hide_index=True)

        if records and st.button("併入資料庫", type="primary", key=f"merge_{cat_id}"):
            work["db"], ins, upd, total = _merge_into(work["db"], cat, records)
            work["log"].append(f"{up.name}：新增 {ins} 筆、更新 {upd} 筆（合併後共 {total:,} 筆）")
            st.success(work["log"][-1])

    st.divider()
    if work["log"]:
        st.markdown(f"**{cat['label']}本次已處理**")
        for line in work["log"]:
            st.markdown(f"- {line}")
    size_mb = len(work["db"]) / 1024 / 1024
    st.download_button(
        f"下載 {fname}（{size_mb:.1f} MB）",
        data=work["db"],
        file_name=fname,
        mime="application/octet-stream",
        disabled=not work["log"],
        key=f"dl_{cat_id}",
    )
    if size_mb > 20:
        st.warning("檔案接近 GitHub 網頁上傳的 25 MB 上限，之後可能要改用 GitHub Desktop 上傳。")
    if work["log"] and st.button("捨棄這個種類本次的所有變更", key=f"reset_{cat_id}"):
        del ss.work[cat_id]
        st.rerun()


# ====================================================================== 導覽
page = st.sidebar.radio("功能", ["🔍 商品查詢", "🛠 資料維護"])
if page == "🔍 商品查詢":
    page_search()
else:
    page_maintain()
