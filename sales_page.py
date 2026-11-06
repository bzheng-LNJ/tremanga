"""
sales_page.py — 📊 賣上分析頁（畫面）。計算在 sales.py。

ctx 由 app.py 傳入：login、current_conn、category_table、PastedData、github、base_dir

商品種類（中文書、文具…）與可選的分析維度設定在 config.py 各種類的 "sales"。
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


MAX_SLICES = 8            # 圓餅圖最多 8 個有顏色的分組，其餘併成「其他」


def _pie_frame(major, value_col):
    """圓餅圖用的資料：去掉合計、去掉 0 與負值；非類別維度的分組太多時，金額小的併成「其他」。"""
    data = major[(major[sales.GROUP] != "合計") & (major[value_col] > 0)][[sales.GROUP, value_col]].copy()
    named = data[~data[sales.GROUP].isin(sales.NEUTRAL_GROUPS)]
    if len(named) > MAX_SLICES:
        keep = set(named[sales.GROUP].head(MAX_SLICES - 1))          # major 已依金額排序
        data[sales.GROUP] = data[sales.GROUP].map(
            lambda g: g if g in keep or g in sales.NEUTRAL_GROUPS else "其他")
        data = data.groupby(sales.GROUP, sort=False, as_index=False)[value_col].sum()
    return data


def _color_map(major, table, mapped):
    """分組 → 顏色。類別維度照對照表順序；其他維度照金額排名給前 7 色，其餘灰色。
    同一次分析的兩張圓餅圖用同一套對應，所以同一個分組在兩張圖裡顏色一樣。"""
    cmap = dict(NEUTRAL)
    cmap[sales.UNSET_GENERIC] = NEUTRAL[cats.UNSET]
    cmap["其他"] = OTHER
    if mapped:
        for i, m in enumerate(table.majors):
            cmap[m] = PALETTE[i] if i < len(PALETTE) else OTHER
    else:
        named = [g for g in major[sales.GROUP] if g != "合計" and g not in sales.NEUTRAL_GROUPS]
        limit = MAX_SLICES - 1 if len(named) > MAX_SLICES else MAX_SLICES
        for i, g in enumerate(named):
            cmap[g] = PALETTE[i] if i < limit else OTHER
    return cmap


def _pie(major, value_col, title, cmap, legend_title):
    data = _pie_frame(major, value_col)
    if data.empty:
        st.caption(f"{title}：沒有可以畫的資料。")
        return
    domain = data[sales.GROUP].tolist()
    data["占比"] = (data[value_col] / data[value_col].sum()).map(lambda v: f"{v:.1%}")
    data["sort_i"] = range(len(data))
    chart = (
        alt.Chart(data, title=title)
        .mark_arc(innerRadius=55, outerRadius=110, stroke="#fcfcfb", strokeWidth=2)
        .encode(
            theta=alt.Theta(f"{value_col}:Q", stack=True),
            color=alt.Color(f"{sales.GROUP}:N", sort=domain, title=legend_title,
                            scale=alt.Scale(domain=domain, range=[cmap.get(g, OTHER) for g in domain]),
                            legend=alt.Legend(title=None, orient="right")),
            order=alt.Order("sort_i:Q"),
            tooltip=[alt.Tooltip(f"{sales.GROUP}:N", title=legend_title),
                     alt.Tooltip(f"{value_col}:Q", format=","), alt.Tooltip("占比:N")],
        )
        .properties(height=280)
    )
    st.altair_chart(chart, use_container_width=True)
    if (major[major[sales.GROUP] != "合計"][value_col] < 0).any():
        st.caption("退貨造成負值的分組不畫進圓餅圖，數字請看下方彙總表。")


def _pick_dimension(spec, df, mapping, key):
    """分析維度：商品資料庫的欄位（config 設定），加上賣上資料裡沒被用到的欄位。"""
    used = {c for c in mapping.values() if c}
    options = [dict(d, source="db") for d in spec.get("dims", [])]
    options += [{"key": c, "label": f"{c}（賣上資料）", "source": "sales", "column": c}
                for c in df.columns if c not in used]
    if not options:
        st.warning("這個商品種類沒有可以用的分析維度。")
        return None
    labels = [o["label"] for o in options]
    choice = st.selectbox("分析維度（依什麼分組）", labels, key=f"sdim_{key}",
                          help="商品資料庫的欄位會用條碼對照；標示「（賣上資料）」的是你貼上的資料本身的欄位。")
    dim = options[labels.index(choice)]
    if dim["source"] == "sales":
        dim = dict(dim, label=dim["column"])
    return dim


def _analysis_tab(ctx):
    table = ctx["category_table"]()
    kinds = [k for k, v in CATEGORIES.items() if v.get("sales")]
    cat_id = st.radio("商品種類", kinds, format_func=lambda k: CATEGORIES[k]["label"],
                      horizontal=True, key="sales_kind")
    cat = CATEGORIES[cat_id]
    spec = cat["sales"]
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
    if not mapping["qty"] and not mapping["stock"]:
        st.warning("請在「欄位對應」指定「賣上數量」或「庫存」其中一個。")
        return
    if mapping["qty"] and not mapping["unit_price"] and not mapping["amount"]:
        st.warning("請在「欄位對應」指定「賣上價格」或「合計賣上金額」其中一個。")
        return

    dim = _pick_dimension(spec, df, mapping, key)
    if dim is None:
        return
    mapped = bool(dim.get("mapped"))

    rows, skipped = sales.parse(df, mapping, dim.get("column"))
    if rows.empty:
        st.error("沒有可以分析的資料，請檢查欄位對應。")
        return

    products = db.fetch_all(ctx["current_conn"](cat_id), cat)
    detail = sales.build_detail(rows, products, table, spec, dim)
    major = sales.pivot_major(detail, table)
    minor = sales.pivot_minor(detail, table)
    names = sales.group_labels(detail)
    top = names[sales.GROUP]
    unit = "冊" if cat_id == "books" else "個"
    shown = dict(names, **sales.count_labels(unit))

    # ---- 概況
    n_missing = (~detail["在資料庫"]).sum()
    unset_label = cats.UNSET if mapped else sales.UNSET_GENERIC
    n_unset = (detail[sales.GROUP] == unset_label).sum()
    n_unknown = (detail[sales.GROUP] == cats.UNKNOWN).sum()
    c = st.columns(4)
    c[0].metric(f"賣上{'冊數' if unit == '冊' else '數量'}", f"{int(detail['賣上數量'].sum()):,}")
    c[1].metric("合計賣上金額", f"{detail['合計賣上金額'].sum():,.0f}")
    c[2].metric("品項數", f"{detail['ISBN(JAN)'].nunique():,}")
    c[3].metric(f"{top}未設定", f"{n_unset:,} 品項")
    notes = []
    if len(skipped):
        notes.append(f"略過 {len(skipped)} 列（條碼無法辨識或沒有數量／庫存，合計列也會被略過）")
    if sales.has_stock(detail):
        stock_total = detail.drop_duplicates("ISBN(JAN)")[sales.STOCK].sum()
        zero_sale = (detail.groupby("ISBN(JAN)")["賣上數量"].sum() == 0).sum()
        notes.append(f"含庫存資料：庫存合計 {int(stock_total):,} {unit}"
                     + (f"；其中 {zero_sale} 個品項只有庫存、沒有賣上（以 0、0 元列入）" if zero_sale else ""))
    if n_missing:
        where = f"「{cats.NOT_FOUND}」" if dim.get("source") != "sales" else "原分組"
        notes.append(f"{n_missing} 個品項在{cat['label']}資料庫查不到（歸在{where}），商品資訊、店頭價格會是空白")
    if n_unknown:
        notes.append(f"{n_unknown} 個品項的類別不在類別對照表，歸在「{cats.UNKNOWN}」，可以到「類別對照表」補上")
    if n_unset and mapped:
        notes.append("類別未設定的商品，可以到「資料維護」的「批次設定類別」補上")
    elif n_unset:
        notes.append(f"{top}空白的商品歸在「{sales.UNSET_GENERIC}」，商品資訊補齊後再分析就會分進去")
    for n in notes:
        st.caption("・" + n)
    if len(skipped):
        with st.expander(f"查看略過的 {len(skipped)} 列"):
            st.dataframe(skipped, hide_index=True)

    # ---- 圓餅圖
    cmap = _color_map(major, table, mapped)
    left, right = st.columns(2)
    with left:
        _pie(major, "冊數", f"賣上{'冊數' if unit == '冊' else '數量'}分布", cmap, top)
    with right:
        _pie(major, "小計金額", "賣上小計金額分布", cmap, top)
    if not mapped and (major[sales.GROUP] != "合計").sum() > MAX_SLICES:
        st.caption(f"{top}超過 {MAX_SLICES} 個，圓餅圖只畫金額前 {MAX_SLICES - 1} 名，其餘併成「其他」；完整數字看下方彙總表。")

    # ---- 彙總表
    st.subheader(f"{top}彙總")
    st.dataframe(_pretty(major).rename(columns=shown), hide_index=True, use_container_width=True)
    if minor is not None:
        with st.expander("類別（小類）彙總"):
            st.dataframe(minor.rename(columns=shown), hide_index=True, use_container_width=True)

    # ---- 商品明細（上方點選分組）
    st.subheader("商品明細")
    present = sales.group_order(detail, table)
    pick = _choose(top, ["全部"] + present, key=f"spick_{key}_{dim['label']}") or "全部"
    view = detail if pick == "全部" else detail[detail[sales.GROUP] == pick]
    if pick != "全部" and mapped:
        minors = list(view[sales.SUB].unique())
        if len(minors) > 1:
            sub = _choose("類別", ["全部"] + sorted(minors), key=f"ssub_{key}_{pick}") or "全部"
            if sub != "全部":
                view = view[view[sales.SUB] == sub]
    st.caption(f"{pick}：{view['ISBN(JAN)'].nunique():,} 品項、{int(view['賣上數量'].sum()):,} {unit}、"
               f"金額 {view['合計賣上金額'].sum():,.0f}")
    st.dataframe(view[sales.detail_cols(detail)], hide_index=True, use_container_width=True,
                 column_config={"ISBN(JAN)": st.column_config.TextColumn(),
                                "商品名": st.column_config.TextColumn(width="large")})

    st.download_button("下載分析結果（Excel）", data=sales.to_excel_bytes(major, minor, detail, unit),
                       file_name=f"賣上分析_{cat['label']}_{top}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def _pretty(major):
    """占比顯示成百分比、金額加千分位（只影響畫面，下載的 Excel 保留數字）。"""
    d = major.copy()
    for c in ("冊數占比", "金額占比"):
        d[c] = d[c].map(lambda v: f"{v:.1%}")
    d["小計金額"] = d["小計金額"].map(lambda v: f"{v:,.0f}")
    if "庫存冊數" in d:
        d["庫存冊數"] = d["庫存冊數"].map(lambda v: "" if pd.isna(v) else f"{int(v):,}")
    d["冊數"] = d["冊數"].map(lambda v: f"{v:,.0f}")
    return d


def _choose(label, options, key):
    """選項少時用可以點選的標籤（st.pills）；很多時（例如上百個廠商）改用下拉選單。"""
    if len(options) <= 16 and hasattr(st, "pills"):
        return st.pills(label, options, selection_mode="single", default="全部", key=key)
    if len(options) <= 16:
        return st.radio(label, options, horizontal=True, key=key)
    return st.selectbox(label, options, key=key)


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
