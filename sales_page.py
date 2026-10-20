"""
sales_page.py — 📊 賣上分析頁（畫面）。計算在 sales.py。

ctx 由 app.py 傳入：login、current_conn、category_table、PastedData、github、base_dir
"""
import hashlib
import os

import altair as alt
import pandas as pd
import streamlit as st

import categories as cats
import db
import importer
import sales
from config import CATEGORIES

# 類別配色：依對照表的大類順序固定給色（色彩跟著類別走，不跟著排名走）。
# 這 8 色已用 dataviz 驗證器檢查過色盲區分度；未設定／不在對照表用灰色。
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
NEUTRAL = {cats.UNSET: "#a3a29b", cats.UNKNOWN: "#6b6a64", cats.NOT_FOUND: "#d3d1c7"}
OTHER = "#c3c2b7"          # 對照表超過 8 個大類時，第 9 個以後共用

SOURCE_CAT = "books"       # 先以中文書為主


def render(ctx):
    st.title("📊 賣上分析")
    editor = ctx["login"]()
    if not editor:
        return

    tab_a, tab_t = st.tabs(["賣上分析", "類別對照表"])
    with tab_t:
        _table_tab(ctx, editor)
    with tab_a:
        _analysis_tab(ctx)


# ====================================================================== 分析
def _read_input(ctx):
    source = st.radio("資料來源", ["上傳檔案", "貼上文字"], horizontal=True, key="sales_src")
    if source == "上傳檔案":
        return st.file_uploader("上傳賣上資料（Excel 或 CSV）", type=["xlsx", "xls", "csv"], key="sales_up")
    text = st.text_area("貼上賣上資料", height=200, key="sales_paste",
                        placeholder="從 Excel 或系統報表複製後貼上，建議連欄位名稱列一起貼。")
    return ctx["PastedData"](text) if text.strip() else None


def _mapping_ui(df, auto, key):
    options = ["（不使用）"] + list(df.columns)
    mapping = {}
    fields = sales.SALES_SPEC["fields"]
    for col, f in zip(st.columns(len(fields)), fields):
        with col:
            default = options.index(auto[f["key"]]) if auto.get(f["key"]) else 0
            choice = st.selectbox(f["label"], options, index=default, key=f"smap_{f['key']}_{key}")
            mapping[f["key"]] = None if choice == "（不使用）" else choice
    return mapping


def _colors(domain, table):
    rng = []
    for m in domain:
        if m in NEUTRAL:
            rng.append(NEUTRAL[m])
        else:
            i = table.majors.index(m) if m in table.majors else 99
            rng.append(PALETTE[i] if i < len(PALETTE) else OTHER)
    return rng


def _pie(major, value_col, share_col, title, table):
    data = major[(major["大類"] != "合計") & (major[value_col] > 0)].copy()
    if data.empty:
        st.caption(f"{title}：沒有可以畫的資料。")
        return
    domain = data["大類"].tolist()
    data["占比"] = (data[value_col] / data[value_col].sum()).map(lambda v: f"{v:.1%}")
    data["sort_i"] = range(len(data))
    chart = (
        alt.Chart(data, title=title)
        .mark_arc(innerRadius=55, outerRadius=110, stroke="#fcfcfb", strokeWidth=2)
        .encode(
            theta=alt.Theta(f"{value_col}:Q", stack=True),
            color=alt.Color("大類:N", sort=domain,
                            scale=alt.Scale(domain=domain, range=_colors(domain, table)),
                            legend=alt.Legend(title=None, orient="right")),
            order=alt.Order("sort_i:Q"),
            tooltip=[alt.Tooltip("大類:N"), alt.Tooltip(f"{value_col}:Q", format=","),
                     alt.Tooltip("占比:N")],
        )
        .properties(height=280)
    )
    st.altair_chart(chart, use_container_width=True)
    if (major[(major["大類"] != "合計")][value_col] < 0).any():
        st.caption("退貨造成負值的類別不畫進圓餅圖，數字請看下方彙總表。")


def _analysis_tab(ctx):
    table = ctx["category_table"]()
    up = _read_input(ctx)
    if up is None:
        st.info("上傳或貼上賣上資料後開始分析。資料只在這次畫面中計算，不會存檔或上傳。")
        return

    data = up.getvalue()
    key = hashlib.md5(data).hexdigest()[:8]
    try:
        sheets = importer.sheet_names(data, up.name)
        sheet = st.selectbox("工作表", sheets, key=f"ssheet_{key}") if len(sheets) > 1 else None
        raw = importer.read_raw(data, up.name, sheet)
    except Exception as e:
        st.error(f"讀不了這份資料：{e}")
        return
    if raw.empty:
        st.warning("沒有讀到任何資料。")
        return

    with st.expander("欄位對應（系統先自動判斷，不對的話在這裡改）", expanded=False):
        headerless = st.checkbox("資料沒有欄位名稱列", value=not importer.has_header(raw, sales.SALES_SPEC),
                                 key=f"snohdr_{key}")
        if headerless:
            df = importer.no_header(raw)
            auto = importer.guess_mapping_by_content(df, sales.SALES_SPEC)
        else:
            df = importer.apply_header(raw, importer.detect_header_row(raw, sales.SALES_SPEC))
            auto = importer.guess_mapping(list(df.columns), sales.SALES_SPEC)
        mapping = _mapping_ui(df, auto, key)

    missing = [f["label"] for f in sales.SALES_SPEC["fields"] if f["key"] in sales.REQUIRED and not mapping[f["key"]]]
    if missing:
        st.warning(f"請在「欄位對應」指定：{'、'.join(missing)}")
        return
    if not mapping["unit_price"] and not mapping["amount"]:
        st.warning("請在「欄位對應」指定「賣上價格」或「合計賣上金額」其中一個。")
        return

    rows, skipped = sales.parse(df, mapping)
    if rows.empty:
        st.error("沒有可以分析的資料，請檢查欄位對應。")
        return

    products = db.fetch_all(ctx["current_conn"](SOURCE_CAT), CATEGORIES[SOURCE_CAT])
    detail = sales.build_detail(rows, products, table)
    major = sales.pivot_major(detail, table)
    minor = sales.pivot_minor(detail, table)

    # ---- 概況
    n_missing = (~detail["在資料庫"]).sum()
    n_unset = (detail["大類"] == cats.UNSET).sum()
    n_unknown = (detail["大類"] == cats.UNKNOWN).sum()
    c = st.columns(4)
    c[0].metric("賣上冊數", f"{int(detail['賣上數量'].sum()):,}")
    c[1].metric("合計賣上金額", f"{detail['合計賣上金額'].sum():,.0f}")
    c[2].metric("品項數", f"{detail['ISBN(JAN)'].nunique():,}")
    c[3].metric("類別未設定", f"{n_unset:,} 品項")
    notes = []
    if len(skipped):
        notes.append(f"略過 {len(skipped)} 列（條碼無法辨識或沒有數量，合計列也會被略過）")
    if n_missing:
        notes.append(f"{n_missing} 個品項在中文書資料庫查不到，歸在「{cats.NOT_FOUND}」，出版社、店頭價格會是空白")
    if n_unknown:
        notes.append(f"{n_unknown} 個品項的類別不在類別對照表，歸在「{cats.UNKNOWN}」，可以到「類別對照表」補上")
    if n_unset:
        notes.append("類別未設定的商品，可以到「資料維護」的「批次設定類別」補上")
    for n in notes:
        st.caption("・" + n)
    if len(skipped):
        with st.expander(f"查看略過的 {len(skipped)} 列"):
            st.dataframe(skipped, hide_index=True)

    # ---- 圓餅圖
    left, right = st.columns(2)
    with left:
        _pie(major, "冊數", "冊數占比", "賣上冊數分布", table)
    with right:
        _pie(major, "小計金額", "金額占比", "賣上小計金額分布", table)

    # ---- 彙總表
    st.subheader("大類彙總")
    st.dataframe(_pretty(major), hide_index=True, use_container_width=True)
    with st.expander("類別（小類）彙總"):
        st.dataframe(minor, hide_index=True, use_container_width=True)

    # ---- 商品明細（上方點選類別）
    st.subheader("商品明細")
    present = [m for m in sales.major_order(table) if m in set(detail["大類"])]
    labels = ["全部"] + present
    pick = _pills("大類", labels, key=f"spick_{key}") or "全部"
    view = detail if pick == "全部" else detail[detail["大類"] == pick]
    if pick != "全部":
        minors = [m for m in view["類別"].replace("", "（空白）").unique()]
        if len(minors) > 1:
            sub = _pills("類別", ["全部"] + sorted(minors), key=f"ssub_{key}_{pick}") or "全部"
            if sub != "全部":
                view = view[view["類別"].replace("", "（空白）") == sub]
    st.caption(f"{pick}：{view['ISBN(JAN)'].nunique():,} 品項、{int(view['賣上數量'].sum()):,} 冊、"
               f"金額 {view['合計賣上金額'].sum():,.0f}")
    st.dataframe(view[sales.DETAIL_COLS], hide_index=True, use_container_width=True,
                 column_config={"ISBN(JAN)": st.column_config.TextColumn(),
                                "商品名": st.column_config.TextColumn(width="large")})

    st.download_button("下載分析結果（Excel）", data=sales.to_excel_bytes(major, minor, detail),
                       file_name="賣上分析.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def _pretty(major):
    """占比顯示成百分比、金額加千分位（只影響畫面，下載的 Excel 保留數字）。"""
    d = major.copy()
    for c in ("冊數占比", "金額占比"):
        d[c] = d[c].map(lambda v: f"{v:.1%}")
    d["小計金額"] = d["小計金額"].map(lambda v: f"{v:,.0f}")
    d["冊數"] = d["冊數"].map(lambda v: f"{v:,.0f}")
    return d


def _pills(label, options, key):
    """有 st.pills 就用（可以點選的標籤），沒有就用橫向單選。"""
    if hasattr(st, "pills"):
        return st.pills(label, options, selection_mode="single", default="全部", key=key)
    return st.radio(label, options, horizontal=True, key=key)


# ====================================================================== 類別對照表
def _table_tab(ctx, editor):
    ss = st.session_state
    path = os.path.join(ctx["base_dir"], cats.FILENAME)
    gh = ctx["github"]()
    if "cat_sha" not in ss:
        ss.cat_sha = gh.fetch(cats.FILENAME)[1] if gh else None
    if ss.get("cat_msg"):
        st.success(ss.pop("cat_msg"))
    st.markdown("賣上分析依這張表把商品的「類別」歸到**大類**。每一列是一個小類和它所屬的大類；"
                "商品資料的類別也可以直接填大類名稱。建檔和批次設定類別的下拉選單也是用這張表。")
    edited = st.data_editor(cats.read_table(path), num_rows="dynamic", hide_index=True,
                            use_container_width=True, key=f"cat_editor_{ss.get('cat_ver', 0)}")
    if st.button("發佈類別對照表", type="primary"):
        content = cats.to_csv_bytes(edited)
        try:
            if gh:
                gh.save(cats.FILENAME, content, ss.cat_sha, f"{editor} 更新類別對照表")
                ss.cat_sha = gh.fetch(cats.FILENAME)[1]
        except Exception as e:
            if "ConflictError" in type(e).__name__:
                st.error("有其他人剛更新過類別對照表。請重新整理頁面，在最新版本上再改一次。")
            else:
                st.error(f"發佈失敗：{e}")
            return
        with open(path, "wb") as f:
            f.write(content)
        ss.cat_ver = ss.get("cat_ver", 0) + 1
        ss.cat_msg = f"✅ 類別對照表已發佈，共 {len(cats.read_table(path))} 個小類。"
        st.rerun()
