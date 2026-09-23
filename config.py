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
    # ↓ 文具、周邊的欄位尚未確定，先用與 JUMP SHOP 相同的三欄，之後再調整
    "stationery": {
        "label": "文具",
        "db": "stationery.db",
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
