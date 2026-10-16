"""
master_sheet.py — 把中文書附加到マスタ登録用的 Google 試算表（例：仮中マ）。

Secrets 需要：
    google_oauth_token（或 gcp_oauth_token）  Google 登入權杖，跟採購工具同一種 token.json 內容
    [master_sheet.book]     ← 978 開頭的商品
    url = "https://docs.google.com/spreadsheets/d/…/edit?gid=…"   ← 網址整串貼上即可（含 gid）
    [master_sheet.other]    ← 978 以外的商品
    url = "…"
    （每段也可以改用採購工具 config.py 的寫法：spreadsheet_id、worksheet_name）

寫入的格式跟採購工具的マスタ登録相同：從 A 欄起依序是
JANコード、商品名、商品名ヨミ、部門売価(0)、本体価格、税区分(0)、税率コード(1)、
レジ大分類(4120)、レジ中分類(78)…，另外補上著者名、出版社名。

欄位位置的判斷方式：
    讀表頭列（config 的 header_row），用欄位名稱找到 JANコード、商品名…在哪一欄；
    再看現有資料裡「13 碼 ISBN」實際在哪一欄，跟表頭的 JANコード 比較，
    算出資料相對表頭的位移（仮中マ 的資料比表頭往左一欄），新資料照同樣的位置寫。
"""
import json
import re

import db


class MasterSheetError(Exception):
    pass


# ---------------------------------------------------------------- 連線
TOKEN_KEYS = ["google_oauth_token", "gcp_oauth_token"]   # 採購工具、分類工具用過的兩種名稱


def _token_info(secrets) -> dict:
    raw = None
    for k in TOKEN_KEYS:
        try:
            raw = secrets[k]
            break
        except Exception:
            continue
    if raw is None:
        raise MasterSheetError("Secrets 裡沒有 Google 登入權杖（google_oauth_token 或 gcp_oauth_token）。")
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            raise MasterSheetError(f"gcp_oauth_token 不是正確的 JSON：{e}")
    info = {k: (list(v) if isinstance(v, (list, tuple)) else v) for k, v in dict(raw).items()}
    return info


def open_worksheet(secrets, conf):
    """
    conf：Secrets 的 [master_sheet]（url，或 spreadsheet_id＋worksheet_name）。
    網址含 gid 就開那個分頁；沒有指定分頁就開第一個。
    """
    import gspread
    from google.oauth2.credentials import Credentials

    conf = dict(conf)
    try:
        creds = Credentials.from_authorized_user_info(_token_info(secrets))
        gc = gspread.authorize(creds)
        if conf.get("url"):
            sh = gc.open_by_url(conf["url"])
        else:
            sh = gc.open_by_key(conf["spreadsheet_id"])
    except MasterSheetError:
        raise
    except KeyError:
        raise MasterSheetError("Secrets 的 [master_sheet] 要有 url 或 spreadsheet_id。")
    except Exception as e:
        raise MasterSheetError(f"無法開啟マスタ登録表（權杖過期或沒有權限？）：{e}")

    m = re.search(r"[#&?]gid=(\d+)", conf.get("url", ""))
    if m:
        ws = sh.get_worksheet_by_id(int(m.group(1)))
        if ws is None:
            raise MasterSheetError(f"找不到 gid={m.group(1)} 的分頁。")
        return ws
    if conf.get("worksheet_name"):
        try:
            return sh.worksheet(conf["worksheet_name"])
        except Exception:
            raise MasterSheetError(f"找不到分頁「{conf['worksheet_name']}」。")
    return sh.get_worksheet(0)


# ---------------------------------------------------------------- 版面判斷
def _h(text) -> str:
    """表頭比對用：正規化、去掉所有空白和換行。"""
    return re.sub(r"\s+", "", db.normalize(text))


def _col_letter(i: int) -> str:
    s, i = "", i + 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def detect_layout(values: list[list[str]], spec: dict) -> dict:
    """
    values：整張表（get_all_values 的結果，第 0 列＝試算表第 1 列）。
    回傳 {"cols": {表頭名稱: 實際寫入的欄號(0 起算)}, "offset": 位移, "jan_col": JAN 實際欄號,
          "existing": 已存在的 ISBN 集合, "next_row": 下一個空白列(1 起算)}
    """
    hr = spec["header_row"] - 1
    if len(values) <= hr:
        raise MasterSheetError(f"表格不到 {spec['header_row']} 列，找不到表頭。")
    header = [_h(v) for v in values[hr]]

    def find(name):
        """name 可以用 | 寫多種表頭寫法，例如「部門売り区分|部門売区分」。"""
        keys = {_h(n) for n in name.split("|")}
        return next((i for i, h in enumerate(header) if h in keys), None)

    targets = list(spec["columns"]) + list(spec["constants"])
    missing = [n.split("|")[0] for n in targets if find(n) is None]
    if missing:
        # 表頭對不上時，改用採購工具的固定欄位位置（從 A 欄起）
        cols = dict(spec["fixed_cols"])
        return _finish(values, spec, cols, offset=None, jan_col=cols[spec["jan_header"]],
                       note=f"表頭找不到：{'、'.join(missing)}，改用固定欄位位置")

    jan_h = find(spec["jan_header"])
    data = values[hr + 1:]

    # 現有資料裡 ISBN 實際在哪一欄
    counts = {}
    for row in data[:200]:
        for i, v in enumerate(row):
            if db.clean_jan(v) and len(re.sub(r"\D", "", v)) in (8, 13):
                counts[i] = counts.get(i, 0) + 1
    if counts:
        jan_col = max(counts, key=counts.get)
        offset = jan_col - jan_h
    else:
        offset = spec.get("default_offset", 0)
        jan_col = jan_h + offset

    cols = {n: find(n) + offset for n in targets}
    if min(cols.values()) < 0:
        raise MasterSheetError("算出的欄位位置不正確（位移後小於 A 欄），請檢查表格。")
    return _finish(values, spec, cols, offset, jan_col, note="")


def _finish(values, spec, cols, offset, jan_col, note):
    data = values[spec["header_row"]:]
    existing = {db.clean_jan(r[jan_col]) for r in data if len(r) > jan_col and db.clean_jan(r[jan_col])}
    last = 0
    for i, r in enumerate(values):
        if len(r) > jan_col and str(r[jan_col]).strip():
            last = i + 1
    next_row = max(last, spec["header_row"]) + 1
    return {"cols": cols, "offset": offset, "jan_col": jan_col, "existing": existing,
            "next_row": next_row, "note": note}


def describe(layout: dict) -> str:
    order = sorted(layout["cols"].items(), key=lambda kv: kv[1])
    text = "、".join(f"{_col_letter(c)} 欄＝{n.split('|')[0]}" for n, c in order)
    return f"{text}（{layout['note']}）" if layout.get("note") else text


# ---------------------------------------------------------------- 寫入
def _num(v):
    """純數字就轉成數字（試算表裡才會是數值、靠右），否則原樣。"""
    s = str(v).strip()
    if re.fullmatch(r"\d+", s):
        return int(s)
    if re.fullmatch(r"\d+\.\d+", s):
        return float(s)
    return s


def build_rows(records: list[dict], layout: dict, spec: dict) -> list[list]:
    width = max(layout["cols"].values()) + 1
    rows = []
    for r in records:
        row = [""] * width
        for header, key in spec["columns"].items():
            v = r.get(key, "")
            row[layout["cols"][header]] = str(v) if header == spec["jan_header"] else _num(v)
        for header, v in spec["constants"].items():
            row[layout["cols"][header]] = v
        rows.append(row)
    return rows


def sheet_spec(kind: str, cat: dict) -> dict:
    """把兩張表共通的版面設定（config.MASTER_SHEETS）和這個種類的欄位對應合在一起。"""
    from config import MASTER_SHEETS
    return dict(MASTER_SHEETS[kind], columns=cat["master_fields"])


def split_by_kind(records: list[dict], cat: dict) -> dict:
    """依條碼開頭分成 {"book": [...], "other": [...]}（978 開頭 → book）。"""
    from config import master_kind
    key = cat["master_fields"]["JANコード"]
    out = {}
    for r in records:
        out.setdefault(master_kind(r.get(key, "")), []).append(r)
    return out


def plan(ws, records: list[dict], spec: dict):
    """回傳 (layout, 要寫入的 records, 已存在而略過的筆數)。"""
    layout = detect_layout(ws.get_all_values(), spec)
    key = spec["columns"][spec["jan_header"]]
    seen, todo = set(layout["existing"]), []
    for r in records:
        code = db.clean_jan(r.get(key))
        if code and code not in seen:
            todo.append(r)
            seen.add(code)
    return layout, todo, len(records) - len(todo)


def append(ws, records: list[dict], spec: dict) -> tuple[int, int, str]:
    """附加還沒在表上的書。回傳 (寫入筆數, 略過筆數, 寫入範圍說明)。"""
    layout, todo, skipped = plan(ws, records, spec)
    if not todo:
        return 0, skipped, ""
    rows = build_rows(todo, layout, spec)
    start = layout["next_row"]
    end = start + len(rows) - 1
    rng = f"A{start}:{_col_letter(len(rows[0]) - 1)}{end}"
    if ws.row_count < end:
        ws.add_rows(end - ws.row_count)
    ws.update(range_name=rng, values=rows, value_input_option="RAW")
    return len(rows), skipped, f"第 {start}～{end} 列"
