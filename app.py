"""
app.py — 中文書籍查詢系統（Streamlit）

兩個頁面：
  🔍 書目查詢：現場人員用，輸入關鍵字 → 查 ISBN → 在表格上選取範圍複製
  🛠 資料維護：上傳出版社建檔檔案 → 併入資料庫 → 下載新的 books.db → 上傳到 GitHub
"""
import os
import tempfile

import streamlit as st

import db
import importer

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "books.db")
MAX_RESULTS = 300

st.set_page_config(page_title="中文書籍查詢系統", page_icon="📚", layout="wide")


@st.cache_resource
def get_conn(_mtime):
    # _mtime 讓 books.db 被換掉（重新部署）時自動重開連線
    conn = db.connect(DB_PATH)
    db.rebuild_search_keys(conn)
    return conn


def current_conn():
    mtime = os.path.getmtime(DB_PATH) if os.path.exists(DB_PATH) else 0
    return get_conn(mtime)


# ====================================================================== 查詢頁
def page_search():
    conn = current_conn()
    st.title("📚 中文書籍查詢系統")
    st.caption(f"目前收錄 {db.count(conn):,} 筆")

    q = st.text_input(
        "關鍵字",
        placeholder="書名、作者、出版社或 ISBN；多個關鍵字用空格分開，例如：航海王 105",
    )
    if not q.strip():
        st.info("輸入關鍵字後按 Enter 查詢。")
        return

    df, truncated = db.search(conn, q, limit=MAX_RESULTS)
    if df.empty:
        st.warning("查無資料。可以試試少打幾個字，或只用書名的一部分查詢。")
        return

    if truncated:
        st.warning(f"結果超過 {MAX_RESULTS} 筆，只顯示前 {MAX_RESULTS} 筆，請加上更多關鍵字縮小範圍。")
    else:
        st.success(f"找到 {len(df)} 筆")

    event = st.dataframe(
        df,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key="results",
        column_config={
            "ISBN": st.column_config.TextColumn(width="medium"),
            "書名": st.column_config.TextColumn(width="large"),
            "定價": st.column_config.TextColumn(width="small"),
        },
    )

    # ---- 單筆複製：點選表格中的一列
    selected = event.selection.rows
    st.subheader("複製單筆")
    if selected:
        row = df.iloc[selected[0]]
        cols = st.columns([1.2, 2, 1.2, 0.6, 1.2])
        for col, field in zip(cols, db.COLUMNS):
            with col:
                st.caption(field)
                st.code(row[field] or "（無資料）", language=None)
        st.caption("按每格右上角的圖示即可複製該欄。")
    else:
        st.caption("點選表格最左邊的方格選取一本書，這裡會出現它的各欄資料，可以單獨複製。"
                   "也可以直接在表格上拖曳選取範圍，按 Ctrl+C（Mac 為 ⌘+C）複製。")



# ====================================================================== 維護頁
def _load_working_db() -> bytes:
    if os.path.exists(DB_PATH):
        with open(DB_PATH, "rb") as f:
            return f.read()
    # 還沒有 books.db 時，建一個空的
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "books.db")
        db.connect(p).close()
        with open(p, "rb") as f:
            return f.read()


def _merge_into(db_bytes: bytes, records: list[dict]):
    """把 records 併進一份 db 的複本，回傳 (新的 db bytes, 新增數, 更新數, 總筆數)"""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "books.db")
        with open(p, "wb") as f:
            f.write(db_bytes)
        conn = db.connect(p)
        inserted, updated = db.upsert(conn, records)
        total = db.count(conn)
        conn.execute("VACUUM")
        conn.close()
        with open(p, "rb") as f:
            return f.read(), inserted, updated, total


def page_maintain():
    st.title("🛠 資料維護")
    st.markdown(
        "1. 上傳出版社寄來的建檔檔案（Excel 或 CSV），確認欄位對應後按「併入資料庫」。可以連續處理好幾個檔案。\n"
        "2. 全部處理完，按「下載 books.db」。\n"
        "3. 到 GitHub 儲存庫**最外層**上傳這個 books.db，覆蓋舊檔。網站會自動更新。"
    )
    st.info("在這頁的操作不會直接改到線上資料，要等步驟 3 上傳後才會生效。")

    ss = st.session_state
    if "work_db" not in ss:
        ss.work_db = _load_working_db()
        ss.log = []

    up = st.file_uploader("上傳建檔檔案", type=["xlsx", "xls", "csv"])
    if up is not None:
        data = up.getvalue()
        try:
            sheets = importer.sheet_names(data, up.name)
            sheet = st.selectbox("工作表", sheets) if len(sheets) > 1 else None
            raw = importer.read_raw(data, up.name, sheet)
        except Exception as e:
            st.error(f"讀不了這個檔案：{e}")
            return

        guess = importer.detect_header_row(raw)
        header_row = st.number_input(
            "表頭在第幾列（欄位名稱那一列）", min_value=1, max_value=max(len(raw), 1), value=guess + 1
        ) - 1
        df = importer.apply_header(raw, header_row)

        st.markdown("**欄位對應**（系統先自動猜，不對的話用下拉選單改）")
        auto = importer.guess_mapping(list(df.columns))
        options = ["（不使用）"] + list(df.columns)
        mapping = {}
        cols = st.columns(len(importer.FIELDS))
        for col, field in zip(cols, importer.FIELDS):
            with col:
                default = options.index(auto[field]) if auto[field] else 0
                choice = st.selectbox(importer.FIELD_LABELS[field], options, index=default,
                                      key=f"map_{field}_{up.name}")
                mapping[field] = None if choice == "（不使用）" else choice

        fixed_pub = ""
        if not mapping["publisher"]:
            fixed_pub = st.text_input("這個檔案沒有出版社欄位，請填出版社名稱（會套用到全部書籍）")

        if not mapping["isbn"] or not mapping["title"]:
            st.warning("ISBN 和書名一定要指定。")
            return

        records, skipped = importer.build_records(df, mapping, fixed_pub)
        st.markdown(f"**預覽**：可匯入 {len(records)} 筆，略過 {len(skipped)} 筆")
        if records:
            preview = [{"ISBN": r["isbn"], "書名": r["title"], "作者": r["author"],
                        "定價": r["price"], "出版社": r["publisher"]} for r in records[:20]]
            st.dataframe(preview, hide_index=True)
        if len(skipped):
            with st.expander(f"查看略過的 {len(skipped)} 筆"):
                st.dataframe(skipped, hide_index=True)

        if records and st.button("併入資料庫", type="primary"):
            ss.work_db, ins, upd, total = _merge_into(ss.work_db, records)
            ss.log.append(f"{up.name}：新增 {ins} 筆、更新 {upd} 筆（合併後共 {total:,} 筆）")
            st.success(ss.log[-1])

    st.divider()
    if ss.log:
        st.markdown("**本次已處理**")
        for line in ss.log:
            st.markdown(f"- {line}")
    size_mb = len(ss.work_db) / 1024 / 1024
    st.download_button(
        f"下載 books.db（{size_mb:.1f} MB）",
        data=ss.work_db,
        file_name="books.db",
        mime="application/octet-stream",
        disabled=not ss.log,
    )
    if size_mb > 20:
        st.warning("檔案接近 GitHub 網頁上傳的 25 MB 上限，之後可能要改用 GitHub Desktop 上傳。")
    if ss.log and st.button("捨棄本次所有變更，重新開始"):
        del ss.work_db
        del ss.log
        st.rerun()


# ====================================================================== 導覽
page = st.sidebar.radio("功能", ["🔍 書目查詢", "🛠 資料維護"])
if page == "🔍 書目查詢":
    page_search()
else:
    page_maintain()
