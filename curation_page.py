"""
curation_page.py — 🏷 商品整理頁（畫面）。規則邏輯在 curation.py。

流程：
  上方篩選（原始廠商、整理狀態、關鍵字）
  ① 整理與確認：系統依規則推測標準欄位 → 人在表格裡修正 → 勾選 → 發佈
  ② 規則表：新增／修改規則（未發佈的修改也會立刻反映在 ① 的推測裡，方便試）
  ③ 候選清單：原始廠商、條碼開頭、品名開頭的統計，幫忙決定要加哪些規則

ctx 由 app.py 傳入：login、github、current_conn、publish_db、edit_db_copy、base_dir
"""
import hashlib
import os

import pandas as pd
import streamlit as st

import curation as cu
import db
from config import CATEGORIES

STD = [("品名", "title")] + list(cu.FIELDS.items())     # [(畫面名稱, key), ...]
SHOW_LIMIT = 500


def render(ctx):
    st.title("🏷 商品整理")
    editor = ctx["login"]()
    if not editor:
        return

    cat_ids = [c for c, v in CATEGORIES.items() if v.get("curation")]
    cat_id = cat_ids[0] if len(cat_ids) == 1 else st.radio(
        "商品種類", cat_ids, format_func=lambda c: CATEGORIES[c]["label"], horizontal=True)
    cat = CATEGORIES[cat_id]
    conn = ctx["current_conn"](cat_id)
    data = db.fetch_all(conn, cat)
    if data.empty:
        st.info(f"{cat['label']}還沒有資料，請先到「資料維護」匯入。")
        return

    ss = st.session_state
    gh = ctx["github"]()
    rules_path = os.path.join(ctx["base_dir"], cu.FILENAME)

    # ------------------------------------------------------------ 篩選
    makers = data["raw_maker"].replace("", "（空白）").value_counts()
    c1, c2, c3 = st.columns([2, 1.2, 1.5])
    pick = c1.multiselect("原始廠商", list(makers.index),
                          format_func=lambda m: f"{m}（{makers[m]}）", placeholder="全部廠商")
    status = c2.radio("狀態", ["未整理", "已整理", "全部"], horizontal=True)
    kw = c3.text_input("品名包含", placeholder="例：エナージェル")

    view = data
    if pick:
        view = view[view["raw_maker"].replace("", "（空白）").isin(pick)]
    if status == "未整理":
        view = view[view["curated_at"] == ""]
    elif status == "已整理":
        view = view[view["curated_at"] != ""]
    if kw.strip():
        k = cu._n(kw)
        view = view[view["name"].map(cu._n).str.contains(k, regex=False)]

    done = (data["curated_at"] != "").sum()
    st.caption(f"全部 {len(data):,} 筆，已整理 {done:,} 筆（{done / len(data):.0%}）｜目前篩選 {len(view):,} 筆")

    tab1, tab2, tab3 = st.tabs(["① 整理與確認", "② 規則表", "③ 候選清單"])

    # 先畫規則表（② 的編輯結果要給 ① 和 ③ 用）
    with tab2:
        rules_df = _rules_tab(ss, gh, editor, rules_path)
    rules = cu.compile_rules(rules_df)
    with tab1:
        _confirm_tab(ss, ctx, cat_id, cat, editor, view, rules)
    with tab3:
        _candidates_tab(ss, view, rules, rules_df)


# ====================================================================== ① 整理與確認
def _confirm_tab(ss, ctx, cat_id, cat, editor, view, rules):
    if ss.get("cur_msg"):
        st.success(ss.pop("cur_msg"))
    if view.empty:
        st.info("目前篩選條件下沒有商品。")
        return

    c1, c2 = st.columns(2)
    reapply = c1.checkbox("已整理的商品也用規則重新推測", value=False,
                          help="不勾時，已整理的商品顯示上次確認的內容。")
    check_all = c2.checkbox("預設全部勾選", value=False)

    rows = view.head(SHOW_LIMIT)
    if len(view) > SHOW_LIMIT:
        st.warning(f"一次最多顯示 {SHOW_LIMIT} 筆，請用上方篩選縮小範圍。")

    table = []
    for _, it in rows.iterrows():
        item = it.to_dict()
        prop, why = cu.propose(item, rules)
        curated = bool(item["curated_at"])
        rec = {"確認": check_all, "狀態": "已整理" if curated else "未整理",
               "JAN": item["jan"], "原始品名": item["name"]}
        for label, key in STD:
            if curated and not reapply:
                rec[label] = item[key]
            else:
                rec[label] = prop.get(key) or item[key]
        rec["推測依據"] = why
        rec["共通分類"] = item["category"]
        table.append(rec)
    df = pd.DataFrame(table)

    def opts(base, col):
        return sorted(set(base) | {v for v in df[col] if v}) + [""]

    locked = ["狀態", "JAN", "原始品名", "推測依據", "共通分類"]
    cfg = {c: st.column_config.TextColumn(disabled=True) for c in locked}
    cfg["原始品名"] = st.column_config.TextColumn(disabled=True, width="large")
    cfg["確認"] = st.column_config.CheckboxColumn(width="small")
    cfg["品名"] = st.column_config.TextColumn(width="large", help="原始品名去掉已拆到其他欄位的文字，可直接修改")
    cfg["類型"] = st.column_config.SelectboxColumn(options=opts(cu.TYPES, "類型"))
    cfg["顏色"] = st.column_config.SelectboxColumn(options=opts(cu.COLORS, "顏色"))

    sig = hashlib.md5(("|".join(df["JAN"]) + str(reapply) + str(check_all)
                       + str(ss.get("rules_ver", 0)) + str(ss.get("cur_ver", 0))).encode()).hexdigest()
    st.caption("系統推測的值可以直接在表格裡修改；確認沒問題的勾選「確認」，再按下方按鈕發佈。"
               "下拉選單沒有的值，請先到規則表或草案討論後再加入。")
    edited = st.data_editor(df, hide_index=True, column_config=cfg, key=f"cur_{sig}",
                            use_container_width=True, height=min(38 * (len(df) + 1), 640))

    chosen = edited[edited["確認"]]
    st.caption(f"已勾選 {len(chosen)} 筆")
    if st.button(f"發佈勾選的 {len(chosen)} 筆", type="primary", disabled=chosen.empty):
        now = db.now_str()
        updates = []
        for _, r in chosen.iterrows():
            u = {"jan": r["JAN"], "curated_by": editor, "curated_at": now}
            u.update({key: r[label] or "" for label, key in STD})
            updates.append(u)

        def work(conn):
            n = db.update_fields(conn, cat, updates)
            db.add_log(conn, editor, "商品整理", 0, n)
            return n

        try:
            with st.spinner("發佈中…"):
                _, n = ctx["publish_db"](
                    cat_id, lambda cur: ctx["edit_db_copy"](cur, cat, work),
                    f"{editor} 整理{cat['label']}（{len(updates)} 筆）")
        except Exception as e:
            st.error(f"發佈失敗，資料沒有變更：{e}")
            return
        ss.cur_msg = f"✅ 已發佈 {n} 筆整理結果。"
        ss.cur_ver = ss.get("cur_ver", 0) + 1
        st.rerun()


# ====================================================================== ② 規則表
def _rules_tab(ss, gh, editor, rules_path) -> pd.DataFrame:
    if "rules_base" not in ss:
        ss.rules_base = cu.read_table(rules_path)
        ss.rules_sha = gh.fetch(cu.FILENAME)[1] if gh else None
    if ss.get("rules_msg"):
        st.success(ss.pop("rules_msg"))

    st.markdown(
        "每一列是一條規則：**比對來源**包含**關鍵字**時，就在**欄位**填入**值**。"
        "關鍵字有多種寫法用 `|` 分隔（例：`エナージェル|energel`）。"
        "**限定廠商**填了的話，只套用在該廠商（填標準廠商名稱）的商品。"
    )
    st.caption("規則的修改會立刻反映在「① 整理與確認」的推測裡，可以邊改邊看；確定後記得按「發佈規則表」。"
               "優先順序：有限定廠商的 → 比對品名的 → 關鍵字較長的。")

    cfg = {
        "欄位": st.column_config.SelectboxColumn(options=list(cu.FIELDS), required=True),
        "比對來源": st.column_config.SelectboxColumn(options=list(cu.SOURCES), default="品名"),
        "關鍵字": st.column_config.TextColumn(width="large"),
    }
    edited = st.data_editor(ss.rules_base, num_rows="dynamic", hide_index=True,
                            column_config=cfg, key=f"rules_{ss.get('rules_ver', 0)}",
                            use_container_width=True)
    ss.rules_current = edited

    if st.button("發佈規則表", type="primary"):
        content = cu.to_csv_bytes(edited)
        try:
            if gh:
                gh.save(cu.FILENAME, content, ss.rules_sha, f"{editor} 更新商品整理規則")
                ss.rules_sha = gh.fetch(cu.FILENAME)[1]
        except Exception as e:
            if "ConflictError" in type(e).__name__:
                st.error("有其他人剛更新過規則表。請重新整理頁面，在最新版本上再改一次。")
            else:
                st.error(f"發佈失敗：{e}")
            return edited
        with open(rules_path, "wb") as f:
            f.write(content)
        ss.rules_base = cu.read_table(rules_path)
        ss.rules_ver = ss.get("rules_ver", 0) + 1
        ss.rules_msg = f"✅ 規則表已發佈，共 {len(ss.rules_base)} 條。"
        st.rerun()
    return edited


def _add_rule(ss, row: dict):
    """快速新增：加在目前編輯中的規則表後面（尚未發佈）。"""
    cur = ss.get("rules_current", ss.rules_base)
    ss.rules_base = pd.concat([cur, pd.DataFrame([row], columns=cu.COLUMNS)], ignore_index=True)
    ss.rules_ver = ss.get("rules_ver", 0) + 1
    ss.rules_msg = f"已加入規則：{row['欄位']}「{row['值']}」←{row['關鍵字']}（尚未發佈，請到規則表確認後發佈）"


# ====================================================================== ③ 候選清單
def _candidates_tab(ss, view, rules, rules_df):
    st.caption("以下統計依照上方的篩選範圍。「已有規則」表示目前規則表已經會命中。")

    with st.form("quick_add", clear_on_submit=True):
        st.markdown("**快速新增規則**")
        c = st.columns([1, 1, 2, 1.5, 1.2])
        field = c[0].selectbox("欄位", list(cu.FIELDS), index=1)
        source = c[1].selectbox("比對來源", list(cu.SOURCES))
        keyword = c[2].text_input("關鍵字", placeholder="可從下方表格複製")
        value = c[3].text_input("值")
        maker = c[4].text_input("限定廠商（可空白）")
        if st.form_submit_button("加入規則表") and keyword.strip() and value.strip():
            _add_rule(ss, {"欄位": field, "值": value.strip(), "比對來源": source,
                           "關鍵字": keyword.strip(), "限定廠商": maker.strip(), "說明": ""})
            st.rerun()

    left, right = st.columns(2)
    with left:
        st.markdown("**原始廠商** → 用來加「廠商」規則")
        mk = view["raw_maker"].value_counts().rename_axis("原始廠商").reset_index(name="商品數")
        mk["已有規則"] = mk["原始廠商"].map(lambda m: cu.covered(m, rules, "maker", "raw_maker"))
        st.dataframe(mk, hide_index=True, use_container_width=True)
    with right:
        st.markdown("**條碼開頭（前 7 碼）** → 用來加「廠商」規則（比對來源選「條碼」）")
        jp = cu.jan_prefix_candidates(view["jan"].tolist(), view["name"].tolist())
        jp["已有規則"] = jp["條碼開頭"].map(lambda j: cu.covered(j, rules, "maker", "jan"))
        st.dataframe(jp, hide_index=True, use_container_width=True)
        st.caption("同一個廠商在不同國家的商品，條碼開頭可能不同，請看品名範例確認。")

    st.markdown("**品名開頭** → 用來加「系列」規則（出現 2 次以上的才列出）")
    only_new = st.checkbox("只顯示還沒有系列規則的", value=True)
    pc = cu.prefix_candidates(view["name"].tolist())
    if not pc.empty:
        pc["已有規則"] = pc["品名開頭"].map(lambda p: cu.covered(p, rules, "series", "name"))
        if only_new:
            pc = pc[~pc["已有規則"]]
    st.dataframe(pc, hide_index=True, use_container_width=True, height=360)

    no_series = sum(1 for _, it in view.iterrows() if "series" not in cu.propose(it.to_dict(), rules)[0])
    st.caption(f"目前篩選範圍內，規則推測不出系列的商品：{no_series} 筆。")
