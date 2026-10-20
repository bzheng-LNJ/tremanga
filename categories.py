"""
categories.py — 中文書類別對照表（大類 ↔ 小類）。

category_rules.csv 兩欄：大類、小類。一個小類只屬於一個大類。
商品資料的「類別」欄填小類（例：動漫-一般大眾），也可以直接填大類名稱（例：漫畫）。
賣上分析時依這張表把類別歸到大類做樞紐分析。
"""
import io
import os

import pandas as pd

import db

FILENAME = "category_rules.csv"
UNSET = "（未設定類別）"
UNKNOWN = "（不在對照表）"
NOT_FOUND = "（資料庫查無）"


def read_table(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        return pd.DataFrame(columns=["大類", "小類"])
    df = pd.read_csv(path, dtype=str, encoding="utf-8-sig").fillna("")
    return df[["大類", "小類"]]


def to_csv_bytes(df: pd.DataFrame) -> bytes:
    df = df[["大類", "小類"]].fillna("").astype(str).apply(lambda c: c.str.strip())
    df = df[(df["大類"] != "") & (df["小類"] != "")].drop_duplicates()
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8-sig")


def _k(s) -> str:
    return db.normalize(s).replace(" ", "")


class Categories:
    def __init__(self, df: pd.DataFrame):
        self.majors = list(dict.fromkeys(df["大類"]))              # 依表格順序
        self.minors = {m: df.loc[df["大類"] == m, "小類"].tolist() for m in self.majors}
        self._to_major = {_k(m): m for m in self.majors}
        for _, r in df.iterrows():
            self._to_major.setdefault(_k(r["小類"]), r["大類"])
        self._canon = {_k(v): v for v in list(df["小類"]) + self.majors}

    def major_of(self, category: str) -> str:
        """類別（小類或大類）→ 大類；空白→未設定；表裡沒有→不在對照表。"""
        if not db.clean_text(category):
            return UNSET
        return self._to_major.get(_k(category), UNKNOWN)

    def canonical(self, category: str) -> str:
        """寫法略有不同（全形半形、空白）時，換成對照表上的寫法。"""
        return self._canon.get(_k(category), db.clean_text(category))

    def options(self) -> list[str]:
        """下拉選單：大類名稱，以及「大類｜小類」。"""
        out = []
        for m in self.majors:
            out.append(m)
            out += [f"{m}｜{s}" for s in self.minors[m]]
        return out

    @staticmethod
    def option_value(option: str) -> str:
        """下拉選單的選項 → 要存進資料的值（小類；只選大類時就存大類）。"""
        return option.split("｜", 1)[-1] if option else ""


def load(path: str) -> Categories:
    return Categories(read_table(path))
