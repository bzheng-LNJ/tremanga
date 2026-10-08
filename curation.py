"""
curation.py — 商品整理的規則邏輯（不含畫面）。

規則表 curation_rules.csv，每一列是一條規則：
    欄位      要填哪個標準欄位：廠商／系列／類型／特別版／粗細／顏色／包裝／備註
    值        要填入的值，例如 Juice
    比對來源  拿哪一欄來比對：品名／原始廠商／共通分類／品番
    關鍵字    比對來源「包含」這段文字就套用；多個寫法用 | 分隔，例如 ジュース|juice
    限定廠商  （可空白）只套用在這個廠商的商品，例如 PILOT
    說明      （可空白）給人看的備註

同一個欄位有多條規則符合時，依序比較：
    1. 有「限定廠商」的優先
    2. 比對來源是品名／品番的，優先於原始廠商／共通分類
       （例：共通分類說是原子筆，但品名有「カートリッジ」→ 以品名規則的「替芯」為準）
    3. 關鍵字越長越優先（「ブルーブラック」勝過「ブルー」）
比對時不分全形半形、大小寫，也忽略空格。

粗細、包裝在沒有規則符合時，會自動從品名判讀（例：0.5mm → 0.5、10色セット → 10 色組）。
"""
import io
import os
import re

import pandas as pd

import db

FILENAME = "curation_rules.csv"
COLUMNS = ["欄位", "值", "比對來源", "關鍵字", "限定廠商", "說明"]

# 標準欄位：畫面名稱 ↔ 資料庫欄位
FIELDS = {"廠商": "maker", "系列": "series", "類型": "type", "特別版": "edition",
          "粗細": "size", "顏色": "color", "包裝": "pack", "備註": "note"}
SOURCES = {"品名": "name", "品番": "model", "原始廠商": "raw_maker", "共通分類": "category"}
SOURCE_PRIORITY = {"name": 2, "model": 2, "raw_maker": 1, "category": 1}

# 草案的標準值清單（下拉選單用；清單外的值也可以手動輸入到規則表）
TYPES = ["鋼珠筆", "原子筆", "中性筆", "鋼筆", "自動鉛筆", "鉛筆", "螢光筆", "簽字筆",
         "彩色筆", "麥克筆", "毛筆", "修正用品", "替芯", "筆芯"]
COLORS = ["黑", "紅", "藍", "綠", "粉紅", "橘", "黃", "紫", "藍黑", "咖啡", "灰", "白",
          "金", "銀", "多色"]

# 第一次使用時的預設規則：顏色字典（照草案）＋替芯判斷
SEED_RULES = [
    ("顏色", "黑", "黒|黑|ブラック|black"),
    ("顏色", "紅", "赤|紅|レッド|red"),
    ("顏色", "藍", "青|藍|ブルー|blue"),
    ("顏色", "綠", "緑|綠|グリーン|green"),
    ("顏色", "粉紅", "ピンク|粉紅|pink"),
    ("顏色", "橘", "オレンジ|橙|橘|orange"),
    ("顏色", "黃", "黄|黃|イエロー|yellow"),
    ("顏色", "紫", "紫|パープル|バイオレット|purple|violet"),
    ("顏色", "藍黑", "ブルーブラック|ブルーブラツク|藍黑|blueblack"),
    ("顏色", "咖啡", "ブラウン|茶色|咖啡|brown"),
    ("顏色", "灰", "グレー|灰|gray|grey"),
    ("顏色", "白", "ホワイト|白|white"),
    ("顏色", "金", "ゴールド|金色|gold"),
    ("顏色", "銀", "シルバー|銀色|silver"),
    ("類型", "替芯", "カートリッジ|替芯|替え芯|リフィル|レフィル|refill"),
]


def _n(s) -> str:
    """比對用：正規化並去掉所有空白。"""
    return re.sub(r"\s+", "", db.normalize(s))


# ---------------------------------------------------------------- 規則表讀寫
def seed_table() -> pd.DataFrame:
    rows = [{"欄位": f, "值": v, "比對來源": "品名", "關鍵字": k, "限定廠商": "", "說明": "預設"}
            for f, v, k in SEED_RULES]
    return pd.DataFrame(rows, columns=COLUMNS)


def read_table(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        return seed_table()
    df = pd.read_csv(path, dtype=str, encoding="utf-8-sig").fillna("")
    for c in COLUMNS:
        if c not in df.columns:
            df[c] = ""
    return df[COLUMNS]


def to_csv_bytes(df: pd.DataFrame) -> bytes:
    df = df.reindex(columns=COLUMNS).fillna("").astype(str).apply(lambda c: c.str.strip())
    df = df[(df["欄位"] != "") & (df["關鍵字"] != "")]
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8-sig")


def compile_rules(df: pd.DataFrame) -> list[dict]:
    """把規則表整理成比對用的清單；欄位或關鍵字空白、欄位名稱不認得的列會略過。"""
    rules = []
    for _, r in df.fillna("").iterrows():
        field = FIELDS.get(str(r["欄位"]).strip())
        source = SOURCES.get(str(r["比對來源"]).strip() or "品名")
        kws = [_n(k) for k in str(r["關鍵字"]).split("|") if _n(k)]
        if not field or not source or not kws:
            continue
        rules.append({
            "field": field, "value": str(r["值"]).strip(), "source": source,
            "keywords": kws, "maker": _n(r["限定廠商"]),
            "label": str(r["欄位"]).strip(),
        })
    return rules


# ---------------------------------------------------------------- 自動判讀
_SIZE_MM = re.compile(r"(\d+(?:\.\d+)?)\s*(?:mm|ミリ|㎜|毫米)", re.I)
_SIZE_BARE = re.compile(r"(?<![\d.])(0\.\d{1,2}|[12]\.\d)(?![\d.])")
_PACK_COLOR = re.compile(r"(\d+)\s*色\s*(?:セット|組|入|パック|set)", re.I)
_PACK_PIECE = re.compile(r"(\d+)\s*(?:本|支)\s*(?:セット|組|入|パック|set)", re.I)


def guess_size(name: str) -> str:
    s = db.normalize(name)
    found = []
    for m in list(_SIZE_MM.finditer(s)) + list(_SIZE_BARE.finditer(s)):
        v = float(m.group(1))
        if 0.1 <= v <= 30:
            t = f"{v:g}"
            if t not in found:
                found.append(t)
    return "/".join(found)


def guess_pack(name: str) -> str:
    s = db.normalize(name)
    if m := _PACK_COLOR.search(s):
        return f"{int(m.group(1))} 色組"
    if m := _PACK_PIECE.search(s):
        return f"{int(m.group(1))} 支組"
    return ""


# ---------------------------------------------------------------- 套用規則
def _best(rules: list[dict], field: str, item: dict, maker: str):
    """回傳 (最優先的規則, 命中的關鍵字)；沒有就 (None, "")。"""
    best, best_score, best_kw = None, None, ""
    for r in rules:
        if r["field"] != field:
            continue
        if r["maker"] and r["maker"] != _n(maker):
            continue
        text = _n(item.get(r["source"], ""))
        hits = [k for k in r["keywords"] if k in text]
        if not hits:
            continue
        kw = max(hits, key=len)
        score = (1 if r["maker"] else 0, SOURCE_PRIORITY[r["source"]], len(kw))
        if best_score is None or score > best_score:
            best, best_score, best_kw = r, score, kw
    return best, best_kw


def propose(item: dict, rules: list[dict]) -> tuple[dict, str]:
    """
    依規則推測一件商品的標準欄位。
    回傳 ({欄位 key: 值}, 依據說明)；推測不出來的欄位不會出現在結果裡。
    """
    out, why = {}, []
    r, kw = _best(rules, "maker", item, "")
    if r:
        out["maker"] = r["value"]
        why.append(f"廠商←{kw}")
    maker = out.get("maker") or item.get("maker", "")

    for field in ["series", "type", "edition", "size", "color", "pack", "note"]:
        r, kw = _best(rules, field, item, maker)
        if r:
            out[field] = r["value"]
            why.append(f"{r['label']}←{kw}")
        elif field == "size" and (v := guess_size(item.get("name", ""))):
            out[field] = v
            why.append(f"粗細←自動({v})")
        elif field == "pack" and (v := guess_pack(item.get("name", ""))):
            out[field] = v
            why.append(f"包裝←自動({v})")
    return out, "；".join(why)


# ---------------------------------------------------------------- 候選清單
_SPLIT = re.compile(r"[\s　・/／()（）\[\]【】「」-]+")


def prefix_candidates(names: list[str], min_count: int = 2) -> pd.DataFrame:
    """
    統計品名開頭（第一段、前兩段）出現的次數，給「系列」規則當參考。
    沒有空格的品名，取開頭連續的片假名／英文／漢字當第一段。
    """
    counts, examples = {}, {}
    for name in names:
        parts = [p for p in _SPLIT.split(db.clean_text(name)) if p]
        if not parts:
            continue
        first = parts[0]
        m = re.match(r"[ァ-ヶー]+|[A-Za-zＡ-Ｚａ-ｚ]+|[一-龥]+", first)
        cands = {first}
        if m and m.group(0) != first:
            cands.add(m.group(0))
        if len(parts) > 1:
            cands.add(f"{parts[0]} {parts[1]}")
        for c in cands:
            if len(c) < 2:
                continue
            counts[c] = counts.get(c, 0) + 1
            examples.setdefault(c, name)
    rows = [{"品名開頭": k, "商品數": v, "品名範例": examples[k]}
            for k, v in counts.items() if v >= min_count]
    df = pd.DataFrame(rows, columns=["品名開頭", "商品數", "品名範例"])
    return df.sort_values(["商品數", "品名開頭"], ascending=[False, True]).reset_index(drop=True)


def covered(text: str, rules: list[dict], field: str, source: str) -> bool:
    """這段文字是否已經有某條規則會命中。"""
    t = _n(text)
    return any(r["field"] == field and r["source"] == source and any(k in t for k in r["keywords"])
               for r in rules)
