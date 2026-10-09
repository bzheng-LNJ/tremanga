"""
config.py — 商品種類設定。

每個種類各自一個 .db 檔，查詢時互不干擾。
要新增種類、改欄位、加欄位別名，都只改這個檔案。

每個欄位的設定：
    key      資料庫裡的欄位名（英文，不要用空格）
    label    畫面上顯示的名稱
    kind     資料整理方式：isbn / jan / price / text
    aliases  出版社或廠商檔案裡可能出現的欄位名稱（自動對應用）
    search   是否列入關鍵字搜尋（預設 True；價格建議 False）

第一個欄位就是主鍵：同一個 ISBN / JAN 只會有一筆。
required    匯入時一定要有值的欄位
fill_in     檔案裡沒有這個欄位時，可以手動輸入一個值套用到整份檔案
append      匯入時接在某欄後面的附加欄位（不另存一欄）。
            例：集數分開寫的建檔檔案，匯入時把集數接到書名後面 →「航海王 105」
"""

CATEGORIES = {
    "books": {
        "label": "中文書",
        "db": "books.db",
        "table": "books",
        "fields": [
            {"key": "isbn", "label": "ISBN", "kind": "isbn",
             "aliases": ["isbn", "isbn13", "isbn-13", "國際標準書號", "書號", "條碼", "國際條碼", "ean", "barcode"]},
            {"key": "title", "label": "書名", "kind": "text",
             "aliases": ["書名", "品名", "商品名稱", "書籍名稱", "中文書名", "title", "品項名稱"]},
            {"key": "author", "label": "作者", "kind": "text",
             "aliases": ["作者", "著者", "作者名", "作/繪者", "作繪者", "原作", "author"]},
            {"key": "price", "label": "定價", "kind": "price", "search": False,
             "aliases": ["定價", "定價(元)", "定價（元）", "售價", "原價", "價格", "建議售價", "price"]},
            {"key": "publisher", "label": "出版社", "kind": "text",
             "aliases": ["出版社", "出版者", "出版公司", "發行", "發行者", "publisher"]},
        ],
        "required": ["isbn", "title"],
        "fill_in": ["publisher"],
        "append": [
            {"key": "volume", "label": "集數", "into": "title",
             "aliases": ["集數", "集", "卷數", "卷", "冊數", "冊", "巻", "巻數", "vol", "vol.", "volume"]},
        ],
        "placeholder": "書名、作者、出版社或 ISBN；多個關鍵字用空格分開，例如：航海王 105",
    },
    "jumpshop": {
        "label": "JUMP SHOP",
        "db": "jumpshop.db",
        "table": "items",
        "fields": [
            {"key": "jan", "label": "JAN", "kind": "jan",
             "aliases": ["janコード", "jan", "jan碼", "jancode", "商品條碼", "條碼", "barcode"]},
            {"key": "name", "label": "商品名稱", "kind": "text",
             "aliases": ["商品名稱", "商品名", "品名", "名稱"]},
            {"key": "price", "label": "店頭賣價", "kind": "price", "search": False,
             "aliases": ["店頭賣價", "店頭売価", "店頭價", "賣價", "售價", "價格", "売価"]},
        ],
        "required": ["jan", "name"],
        "fill_in": [],
        "placeholder": "商品名稱或 JAN；多個關鍵字用空格分開，例如：航海王 吊飾",
    },
    # 文具：前 6 欄由建檔檔案匯入（原始資料），後面的標準欄位只在「商品整理」頁填寫。
    "stationery": {
        "label": "文具",
        "db": "stationery.db",
        "table": "items",
        "fields": [
            # ---- 匯入欄位（原始資料，保留原樣）
            {"key": "jan", "label": "JAN", "kind": "jan",
             "aliases": ["janコード", "jan", "jan碼", "商品條碼", "條碼", "barcode"]},
            {"key": "name", "label": "原始品名", "kind": "text",
             "aliases": ["商品名稱", "商品名", "品名", "名稱", "原始品名"]},
            {"key": "price", "label": "售價", "kind": "price", "search": False,
             "aliases": ["售價", "店頭価格", "店頭価格(円)", "店頭賣價", "定價", "賣價", "價格", "建議售價"]},
            {"key": "model", "label": "品番", "kind": "text",
             "aliases": ["品番", "型番", "型號", "品號"]},
            {"key": "raw_maker", "label": "原始廠商", "kind": "text",
             "aliases": ["出版社・メーカー", "出版社･メーカー", "メーカー", "メーカー名", "廠商", "品牌"]},
            {"key": "category", "label": "共通分類", "kind": "text",
             "aliases": ["共通分類", "分類"]},
            # ---- 標準欄位（商品整理頁填寫，匯入時不會被覆蓋）
            {"key": "maker", "label": "廠商", "kind": "text", "import": False},
            # 整理後品名：原始品名去掉已經拆到其他欄位的部分（之後統一成中文名稱也放這裡）
            {"key": "title", "label": "品名", "kind": "text", "import": False},
            {"key": "series", "label": "系列", "kind": "text", "import": False},
            {"key": "type", "label": "類型", "kind": "text", "import": False},
            {"key": "edition", "label": "特別版", "kind": "text", "import": False},
            {"key": "size", "label": "粗細", "kind": "text", "import": False},
            {"key": "color", "label": "顏色", "kind": "text", "import": False},
            {"key": "note", "label": "備註", "kind": "text", "import": False},
            {"key": "curated_by", "label": "整理者", "kind": "text", "import": False,
             "search": False, "show": False},
            {"key": "curated_at", "label": "整理時間", "kind": "text", "import": False,
             "search": False, "show": False},
        ],
        # 查詢結果的欄位順序
        "display": ["maker", "jan", "title", "series", "size", "color", "edition"],
        # 查詢結果的「品名」：有整理後品名就顯示它，還沒整理的顯示原始品名
        "display_fallback": {"title": "name"},
        "sort_key": "title",
        "curation": True,
        "required": ["jan", "name"],
        "fill_in": [],
        "placeholder": "廠商、系列、商品名稱或 JAN，例如：飛龍 energel",
    },
    "goods": {
        "label": "周邊",
        "db": "goods.db",
        "table": "items",
        "fields": [
            {"key": "jan", "label": "JAN", "kind": "jan",
             "aliases": ["janコード", "jan", "jan碼", "商品條碼", "條碼", "barcode"]},
            {"key": "name", "label": "商品名稱", "kind": "text",
             "aliases": ["商品名稱", "商品名", "品名", "名稱"]},
            {"key": "price", "label": "售價", "kind": "price", "search": False,
             "aliases": ["售價", "定價", "賣價", "價格", "建議售價"]},
        ],
        "required": ["jan", "name"],
        "fill_in": [],
        "placeholder": "商品名稱或 JAN",
    },
}
