"""
app.py — 中文書籍查詢系統（Streamlit）

  🔍 商品查詢：選商品種類 → 輸入關鍵字 → 在表格上選取範圍複製
  🛠 資料維護：密碼登入 → 選商品種類 → 上傳建檔檔案 → 預覽 → 發佈（直接寫回 GitHub）

商品種類與欄位設定在 config.py。每個種類各自一個 .db 檔，查詢時不會混在一起。
"""
import hashlib
import hmac
import os
import tempfile

import streamlit as st

import db
import importer
import synonyms as syn
import curation_page
import master_sheet as ms
import categories as cats
import sales_page
from config import CATEGORIES, MASTER_SHEETS

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MAX_RESULTS = 300
CAT_IDS = list(CATEGORIES)

st.set_page_config(page_title="中文書籍查詢系統", page_icon="📚", layout="wide")


def db_path(cat_id: str) -> str:
    return os.path.join(BASE_DIR, CATEGORIES[cat_id]["db"])


@st.cache_resource
def get_conn(cat_id: str, mtime: float):
    # mtime（檔案修改時間）讓 .db 被換掉或發佈後自動重開連線。
    # 注意：參數名稱不能以底線開頭，否則 Streamlit 快取會忽略它。
    cat = CATEGORIES[cat_id]
    conn = db.connect(db_path(cat_id), cat)
    db.rebuild_search_keys(conn, cat)
    return conn


def current_conn(cat_id: str):
    p = db_path(cat_id)
    return get_conn(cat_id, os.path.getmtime(p) if os.path.exists(p) else 0)


SYN_PATH = os.path.join(BASE_DIR, syn.FILENAME)


@st.cache_data
def _synonym_lookup(mtime: float):
    return syn.build_lookup(syn.read_table(SYN_PATH))


def synonym_lookup() -> dict:
    return _synonym_lookup(os.path.getmtime(SYN_PATH) if os.path.exists(SYN_PATH) else 0)


CAT_RULES_PATH = os.path.join(BASE_DIR, cats.FILENAME)


@st.cache_data
def _category_table(mtime: float):
    return cats.load(CAT_RULES_PATH)


def category_table() -> "cats.Categories":
    return _category_table(os.path.getmtime(CAT_RULES_PATH) if os.path.exists(CAT_RULES_PATH) else 0)


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

    df, truncated = db.search(conn, cat, q, limit=MAX_RESULTS, synonyms=synonym_lookup())
    if df.empty:
        st.warning("查無資料。可以試試少打幾個字，或只用名稱的一部分查詢。")
        return
    if truncated:
        st.warning(f"結果超過 {MAX_RESULTS} 筆，只顯示前 {MAX_RESULTS} 筆，請加上更多關鍵字縮小範圍。")
    else:
        st.success(f"找到 {len(df)} 筆")

    preset = cat.get("widths", {})
    widths = {f["label"]: preset.get(f["key"]) or
              ("large" if i == 1 else "small" if f["kind"] == "price" else "medium")
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
def _empty_db_bytes(cat) -> bytes:
    with tempfile.TemporaryDirectory() as d:
        tmp = os.path.join(d, "x.db")
        db.connect(tmp, cat).close()
        with open(tmp, "rb") as f:
            return f.read()


def _merge(db_bytes: bytes | None, cat, records, editor: str, filename: str, fill_only: bool = False):
    """把 records 併進 db 的複本，回傳 (新的 db bytes, 新增數, 更新數, 總筆數)"""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "x.db")
        with open(p, "wb") as f:
            f.write(db_bytes or _empty_db_bytes(cat))
        conn = db.connect(p, cat)
        inserted, updated = db.upsert(conn, cat, records, fill_only=fill_only)
        db.add_log(conn, editor, filename, inserted, updated)
        total = db.count(conn, cat)
        conn.execute("VACUUM")
        conn.close()
        with open(p, "rb") as f:
            return f.read(), inserted, updated, total


def publish_db(cat_id: str, merge_fn, message: str):
    """
    共用的發佈流程：有 GitHub 設定就抓最新版合併後寫回（衝突自動重試），
    沒有就在本機合併。成功後本機檔案也同步更新。回傳 merge_fn 的結果。
    """
    cat = CATEGORIES[cat_id]
    gh = _github()
    if gh:
        result = gh.publish(cat["db"], merge_fn, message)
    else:
        p = db_path(cat_id)
        cur = open(p, "rb").read() if os.path.exists(p) else None
        result = merge_fn(cur)
    with open(db_path(cat_id), "wb") as f:
        f.write(result[0])
    return result


def edit_db_copy(db_bytes: bytes | None, cat, fn):
    """在 db 的複本上執行 fn(conn)，回傳 (新的 db bytes, fn 的回傳值)。"""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "x.db")
        with open(p, "wb") as f:
            f.write(db_bytes or _empty_db_bytes(cat))
        conn = db.connect(p, cat)
        out = fn(conn)
        conn.execute("VACUUM")
        conn.close()
        with open(p, "rb") as f:
            return f.read(), out


def _github():
    """Secrets 有 [github] 設定就回傳 GitHubStore，否則 None（改用下載模式）。"""
    try:
        g = st.secrets["github"]
        from github_store import GitHubStore
        return GitHubStore(g["token"], g["repo"], g.get("branch", "main"))
    except Exception:          # 沒有 Secrets 或缺少 [github]
        return None


def _login() -> str | None:
    """用 Secrets 的 [editors] 密碼登入，回傳登錄者名稱。"""
    ss = st.session_state
    if ss.get("editor"):
        c1, c2 = st.columns([4, 1])
        c1.caption(f"登錄者：{ss.editor}")
        if c2.button("登出"):
            del ss.editor
            st.rerun()
        return ss.editor

    try:
        editors = dict(st.secrets["editors"])
    except Exception:
        st.error("尚未設定登錄密碼（Secrets 的 [editors]），請聯絡管理者。")
        return None

    pw = st.text_input("請輸入登錄密碼", type="password")
    if pw:
        name = next((n for n, p in editors.items() if hmac.compare_digest(str(p), pw)), None)
        if name:
            ss.editor = name
            st.rerun()
        st.error("密碼不正確。")
    return None


class PastedData:
    """把貼上的文字包裝成跟上傳檔案一樣的介面（name、getvalue），後續流程共用。"""

    def __init__(self, text: str):
        self._data = text.encode("utf-8")
        self.name = f"貼上資料_{hashlib.md5(self._data).hexdigest()[:6]}.txt"

    def getvalue(self) -> bytes:
        return self._data


def _prepare_records(cat_id: str, cat, up):
    """讀檔 → 欄位對應 → 預覽。回傳 records；還不能匯入時回傳 None。"""
    ifields = [f for f in cat["fields"] if f.get("import", True)]
    data = up.getvalue()
    try:
        sheets = importer.sheet_names(data, up.name)
        sheet = st.selectbox("工作表", sheets) if len(sheets) > 1 else None
        raw = importer.read_raw(data, up.name, sheet)
    except Exception as e:
        st.error(f"讀不了這個檔案：{e}")
        return None

    if raw.empty:
        st.warning("沒有讀到任何資料。")
        return None

    headerless = st.checkbox(
        "資料沒有欄位名稱列（第一列就是商品資料）",
        value=not importer.has_header(raw, cat),
        key=f"nohdr_{cat_id}_{up.name}",
    )
    if headerless:
        df = importer.no_header(raw)
        auto = importer.guess_mapping_by_content(df, cat)
    else:
        guess = importer.detect_header_row(raw, cat)
        header_row = st.number_input(
            "表頭在第幾列（欄位名稱那一列）", min_value=1, max_value=max(len(raw), 1), value=guess + 1
        ) - 1
        df = importer.apply_header(raw, header_row)
        auto = importer.guess_mapping(list(df.columns), cat)

    st.markdown("**欄位對應**（系統先自動猜，不對的話用下拉選單改）")
    options = ["（不使用）"] + list(df.columns)
    mapping = {}
    for col, f in zip(st.columns(len(ifields)), ifields):
        with col:
            default = options.index(auto[f["key"]]) if auto[f["key"]] else 0
            choice = st.selectbox(f["label"], options, index=default,
                                  key=f"map_{cat_id}_{f['key']}_{up.name}")
            mapping[f["key"]] = None if choice == "（不使用）" else choice

    for a in cat.get("append", []):
        into = next(f["label"] for f in ifields if f["key"] == a["into"])
        default = options.index(auto[a["key"]]) if auto[a["key"]] else 0
        choice = st.selectbox(
            f"{a['label']}（檔案裡{a['label']}另外一欄時才指定，會接在{into}後面）",
            options, index=default, key=f"map_{cat_id}_{a['key']}_{up.name}")
        mapping[a["key"]] = None if choice == "（不使用）" else choice

    fill_values = {}
    for f in ifields:
        if f["key"] in cat["fill_in"] and not mapping[f["key"]]:
            if f.get("choices") == "category":
                choice = st.selectbox(
                    f"這個檔案沒有{f['label']}欄位，請選{f['label']}（會套用到全部商品；不選就留空白）",
                    [""] + category_table().options(), key=f"fill_{cat_id}_{f['key']}")
                fill_values[f["key"]] = cats.Categories.option_value(choice)
            else:
                fill_values[f["key"]] = st.text_input(
                    f"這個檔案沒有{f['label']}欄位，請填{f['label']}（會套用到全部商品）",
                    key=f"fill_{cat_id}_{f['key']}")

    labels = {f["key"]: f["label"] for f in ifields}
    lacking = [labels[k] for k in cat["required"] if not mapping[k]]
    if lacking:
        st.warning(f"{'、'.join(lacking)} 一定要指定。")
        return None

    records, skipped = importer.build_records(df, mapping, cat, fill_values)

    # 類別：換成對照表上的寫法，並提醒對照表裡沒有的類別
    for f in ifields:
        if f.get("choices") == "category":
            table = category_table()
            unknown = {}
            for r in records:
                if r.get(f["key"]):
                    r[f["key"]] = table.canonical(r[f["key"]])
                    if table.major_of(r[f["key"]]) == cats.UNKNOWN:
                        unknown[r[f["key"]]] = unknown.get(r[f["key"]], 0) + 1
            if unknown:
                st.warning("這些類別不在類別對照表裡（賣上分析會歸到「不在對照表」）："
                           + "、".join(f"{k}（{v} 筆）" for k, v in unknown.items()))
    st.markdown(f"**預覽**：可登錄 {len(records)} 筆，略過 {len(skipped)} 筆")
    if records:
        st.dataframe([{labels[k]: v for k, v in r.items()} for r in records[:20]], hide_index=True)
    if len(skipped):
        with st.expander(f"查看略過的 {len(skipped)} 筆"):
            st.dataframe(skipped, hide_index=True)
    return records or None


def _master_confs(cat) -> dict:
    """
    這個種類可寫入的マスタ登録表：{"book": 設定, "other": 設定}，只列出 Secrets 有設定的。
    舊寫法 [master_sheet] url=… 視為 book 表。
    """
    if not cat.get("master_fields"):
        return {}
    try:
        root = dict(st.secrets["master_sheet"])
    except Exception:
        return {}
    out = {}
    for kind in ("book", "other"):
        try:
            conf = dict(root[kind])
        except Exception:
            continue
        if conf.get("url") or conf.get("spreadsheet_id"):
            out[kind] = conf
    if "book" not in out and (root.get("url") or root.get("spreadsheet_id")):
        out["book"] = {k: v for k, v in root.items() if k in ("url", "spreadsheet_id", "worksheet_name")}
    return out


def _write_master(cat, records) -> tuple[bool, str]:
    """依條碼分到 book／other 表各自附加，回傳 (全部成功與否, 訊息)。"""
    confs = _master_confs(cat)
    ok_all, lines = True, []
    for kind, recs in ms.split_by_kind(records, cat).items():
        label = MASTER_SHEETS[kind]["label"]
        if kind not in confs:
            lines.append(f"・{label}：{len(recs)} 筆沒有寫入（Secrets 還沒設定 [master_sheet.{kind}]）")
            continue
        try:
            ws = ms.open_worksheet(st.secrets, confs[kind])
            n, skipped, where = ms.append(ws, recs, ms.sheet_spec(kind, cat))
        except Exception as e:
            ok_all = False
            lines.append(f"・{label}：寫入失敗 — {e}")
            continue
        if n == 0:
            lines.append(f"・{label}：{skipped} 筆都已經在表上，沒有新增")
        else:
            extra = f"，{skipped} 筆已在表上而略過" if skipped else ""
            lines.append(f"・{label}：已附加 {n} 筆（{where}）{extra}")
    return ok_all, "マスタ登録表\n\n" + "\n".join(lines)


def _master_section(cat, records, done_key):
    """發佈按鈕上方的選項＋檢查；回傳是否要同時寫入。"""
    confs = _master_confs(cat)
    if not confs:
        return False
    groups = ms.split_by_kind(records, cat)
    summary = "、".join(f"{MASTER_SHEETS[k]['label']} {len(v)} 筆" for k, v in groups.items())
    to_master = st.checkbox(f"同時附加到マスタ登録表（{summary}）", value=True, key=f"tomaster_{done_key}",
                            help="978 開頭寫入書籍表，其他寫入非書籍表；表上已經有的條碼會略過。")
    if to_master and st.button("檢查マスタ登録表（寫入位置、會新增幾筆）", key=f"chk_{done_key}"):
        for kind, recs in groups.items():
            label = MASTER_SHEETS[kind]["label"]
            if kind not in confs:
                st.warning(f"{label}：Secrets 還沒設定 [master_sheet.{kind}]，這 {len(recs)} 筆不會寫入。")
                continue
            try:
                with st.spinner(f"讀取{label}表…"):
                    ws = ms.open_worksheet(st.secrets, confs[kind])
                    layout, todo, skipped = ms.plan(ws, recs, ms.sheet_spec(kind, cat))
                st.info(f"**{label}**：將從第 {layout['next_row']} 列開始附加 {len(todo)} 筆，"
                        f"{skipped} 筆已在表上會略過。\n\n寫入位置：{ms.describe(layout)}")
            except Exception as e:
                st.error(f"{label}：{e}")
    return to_master


def _batch_category_section(cat_id, cat, editor):
    """批次設定類別：篩選已登錄的商品，一次套用同一個類別。"""
    field = next((f for f in cat["fields"] if f.get("choices") == "category"), None)
    if not field:
        return
    ss = st.session_state
    st.divider()
    with st.expander("批次設定類別（已登錄的商品）", expanded=bool(ss.get("bulk_msg"))):
        if ss.get("bulk_msg"):
            st.success(ss.pop("bulk_msg"))
        data = db.fetch_all(current_conn(cat_id), cat)
        if data.empty:
            st.caption("還沒有資料。")
            return
        key, pk = field["key"], db.key_field(cat)
        name_key = cat["fields"][1]["key"]
        c1, c2, c3 = st.columns([1.2, 2, 1.5])
        scope = c1.radio("對象", ["類別空白的", "全部"], key="bulk_scope")
        pubs = sorted(p for p in data["publisher"].unique() if p) if "publisher" in data else []
        pick = c2.multiselect("出版社", pubs, placeholder="全部出版社", key="bulk_pub")
        kw = c3.text_input("書名包含", key="bulk_kw")

        view = data
        if scope == "類別空白的":
            view = view[view[key] == ""]
        if pick:
            view = view[view["publisher"].isin(pick)]
        if kw.strip():
            k = db.normalize(kw).replace(" ", "")
            view = view[view[name_key].map(lambda t: k in db.normalize(t).replace(" ", ""))]

        st.caption(f"符合條件：{len(view):,} 筆（全部 {len(data):,} 筆，其中類別空白 {(data[key] == '').sum():,} 筆）")
        if view.empty:
            return
        st.dataframe(view[[pk, name_key, "publisher", key]].head(200).rename(
            columns={pk: db.field_label(cat, pk), name_key: db.field_label(cat, name_key),
                     "publisher": "出版社", key: "目前類別"}),
            hide_index=True, use_container_width=True, height=240)
        if len(view) > 200:
            st.caption("只列出前 200 筆，套用時會包含全部符合的商品。")

        choice = st.selectbox("要套用的類別", [""] + category_table().options(), key="bulk_choice")
        value = cats.Categories.option_value(choice)
        if st.button(f"把這 {len(view):,} 筆設為「{value or '—'}」並發佈", type="primary",
                     disabled=not value, key="bulk_go"):
            updates = [{pk: r, key: value} for r in view[pk]]

            def work(conn):
                n = db.update_fields(conn, cat, updates)
                db.add_log(conn, editor, f"批次設定類別：{value}", 0, n)
                return n
            try:
                with st.spinner("發佈中…"):
                    _, n = publish_db(cat_id, lambda cur: edit_db_copy(cur, cat, work),
                                      f"{editor} 批次設定{cat['label']}類別「{value}」（{len(updates)} 筆）")
            except Exception as e:
                st.error(f"發佈失敗，資料沒有變更：{e}")
                return
            ss.bulk_msg = f"✅ 已把 {n:,} 筆的類別設為「{value}」。"
            st.rerun()


def page_maintain():
    st.title("🛠 資料維護")
    editor = _login()
    if not editor:
        return

    cat_id = pick_category("maintain_cat")
    cat = CATEGORIES[cat_id]
    gh = _github()
    ss = st.session_state

    if gh:
        st.markdown(f"上傳{cat['label']}的建檔檔案（Excel 或 CSV），確認預覽沒問題後按「發佈」，查詢頁就會更新。")
    else:
        st.warning("尚未設定 GitHub 連線（Secrets 的 [github]），目前為下載模式：發佈後需手動上傳 .db 到 GitHub。")

    source = st.radio("資料來源", ["上傳檔案", "貼上文字"], horizontal=True, key=f"src_{cat_id}")
    if source == "上傳檔案":
        up = st.file_uploader("上傳建檔檔案", type=["xlsx", "xls", "csv"], key=f"up_{cat_id}")
    else:
        text = st.text_area(
            "貼上資料",
            height=220,
            key=f"paste_{cat_id}",
            placeholder="從 Excel、Google 試算表或信件內文複製後貼上。\n"
                        "可以連欄位名稱列一起貼，也可以只貼資料。",
        )
        up = PastedData(text) if text.strip() else None
    if up is not None:
        records = _prepare_records(cat_id, cat, up)
        done_key = f"{cat_id}:{up.name}:{hashlib.md5(up.getvalue()).hexdigest()}"
        already = done_key in ss.get("published", {})

        fill_only = False
        if records and not already:
            fill_only = st.radio(
                "已經登錄過的商品",
                ["有新資料就更新", "只補上空白的欄位"],
                horizontal=True, key=f"mode_{cat_id}",
                help="選「只補上空白的欄位」時，已登錄商品原本有值的欄位（例如品名、售價）一律不會被改動。",
            ) == "只補上空白的欄位"

        to_master = _master_section(cat, records, done_key) if records and not already else False

        if already:
            st.success(ss.published[done_key])
            msg = ss.get("master_msg", {}).get(done_key)
            if msg:
                (st.success if msg[0] else st.error)(msg[1])
            if done_key in ss.get("master_pending", {}):
                if st.button("重試寫入マスタ登録表", key=f"retry_{done_key}"):
                    with st.spinner("寫入マスタ登録表…"):
                        ok, text = _write_master(cat, ss.master_pending[done_key])
                    ss.master_msg[done_key] = (ok, text)
                    if ok:
                        del ss.master_pending[done_key]
                    st.rerun()
        elif records and st.button("發佈", type="primary", key=f"pub_{cat_id}"):
            merge_fn = lambda cur: _merge(cur, cat, records, editor, up.name, fill_only)
            try:
                with st.spinner("發佈中…"):
                    if gh:
                        msg = f"{editor} 登錄{cat['label']}：{up.name}（{len(records)} 筆）"
                        new_bytes, ins, upd, total = gh.publish(cat["db"], merge_fn, msg)
                    else:
                        cur = open(db_path(cat_id), "rb").read() if os.path.exists(db_path(cat_id)) else None
                        new_bytes, ins, upd, total = merge_fn(cur)
            except Exception as e:
                st.error(f"發佈失敗，資料沒有變更：{e}")
                return

            with open(db_path(cat_id), "wb") as f:      # 本機也立刻更新，查詢頁馬上查得到
                f.write(new_bytes)
            result = f"{up.name}：新增 {ins} 筆、更新 {upd} 筆（{cat['label']}共 {total:,} 筆）"
            ss.setdefault("published", {})[done_key] = "✅ 已發佈 — " + result
            ss.setdefault("last_bytes", {})[cat_id] = new_bytes
            if to_master:
                with st.spinner("寫入マスタ登録表…"):
                    ok, text = _write_master(cat, records)
                ss.setdefault("master_msg", {})[done_key] = (ok, text)
                if not ok:
                    ss.setdefault("master_pending", {})[done_key] = records
            st.rerun()

    if not gh and ss.get("last_bytes", {}).get(cat_id):
        st.download_button(f"下載 {cat['db']}", data=ss.last_bytes[cat_id],
                           file_name=cat["db"], mime="application/octet-stream")

    _batch_category_section(cat_id, cat, editor)

    st.divider()
    st.markdown(f"**{cat['label']}最近的登錄紀錄**")
    logs = db.recent_logs(current_conn(cat_id))
    if logs.empty:
        st.caption("還沒有紀錄。")
    else:
        st.dataframe(logs, hide_index=True)


# ====================================================================== 同義詞表
def page_synonyms():
    st.title("📖 同義詞表")
    editor = _login()
    if not editor:
        return

    st.markdown(
        "每一列是一組**指同一個東西的不同寫法**，名稱1、名稱2……往右填，用不到的格子留空。"
        "例如：排球少年｜ハイキュー｜Haikyu、百樂｜PILOT｜パイロット。\n\n"
        "查詢時輸入其中任何一個名稱，就會同時找出其他寫法的商品。所有商品種類共用這張表。"
    )
    st.caption("在表格最下方的空白列輸入可新增一組；選取列後按鍵盤 Delete 可刪除。改完按「發佈」。")

    gh = _github()
    ss = st.session_state
    if "syn_sha" not in ss:                      # 記下開始編輯時的版本，發佈時用來檢查衝突
        ss.syn_sha = gh.fetch(syn.FILENAME)[1] if gh else None

    table = syn.read_table(SYN_PATH)
    if ss.get("syn_msg"):
        st.success(ss.pop("syn_msg"))
        if not gh and ss.get("syn_bytes"):
            st.download_button(f"下載 {syn.FILENAME}", data=ss.syn_bytes,
                               file_name=syn.FILENAME, mime="text/csv")

    edited = st.data_editor(table, num_rows="dynamic", hide_index=True,
                            key=f"syn_editor_{ss.get('syn_ver', 0)}",
                            column_config={c: st.column_config.TextColumn() for c in table.columns})

    if st.button("發佈", type="primary"):
        content = syn.to_csv_bytes(edited)
        try:
            if gh:
                gh.save(syn.FILENAME, content, ss.syn_sha, f"{editor} 更新同義詞表")
                ss.syn_sha = gh.fetch(syn.FILENAME)[1]
        except Exception as e:
            if "ConflictError" in type(e).__name__:
                st.error("有其他人剛更新過同義詞表。請重新整理頁面，在最新版本上再改一次。")
            else:
                st.error(f"發佈失敗，資料沒有變更：{e}")
            return
        with open(SYN_PATH, "wb") as f:
            f.write(content)
        # 換一個編輯器 key，讓表格以剛發佈的內容重新開始（避免新增列被重複套用）
        ss.syn_ver = ss.get("syn_ver", 0) + 1
        ss.syn_msg = f"✅ 已發佈，共 {len(syn.read_table(SYN_PATH))} 組同義詞。"
        ss.syn_bytes = content
        st.rerun()


# ====================================================================== 導覽
page = st.sidebar.radio("功能", ["🔍 商品查詢", "🛠 資料維護", "📖 同義詞表", "🏷 商品整理", "📊 賣上分析"])
if page == "🔍 商品查詢":
    page_search()
elif page == "🛠 資料維護":
    page_maintain()
elif page == "📖 同義詞表":
    page_synonyms()
elif page == "📊 賣上分析":
    sales_page.render({
        "login": _login, "current_conn": current_conn, "category_table": category_table,
        "PastedData": PastedData, "github": _github, "base_dir": BASE_DIR,
    })
else:
    curation_page.render({
        "login": _login, "github": _github, "current_conn": current_conn,
        "publish_db": publish_db, "edit_db_copy": edit_db_copy, "base_dir": BASE_DIR,
    })
