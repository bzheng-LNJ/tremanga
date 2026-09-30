"""
github_store.py — 直接讀寫 GitHub 儲存庫裡的 .db 檔。

需要在 Streamlit 的 Secrets 設定：
    [github]
    token  = "github_pat_..."     # 只開放這個儲存庫 Contents 讀寫權限的權杖
    repo   = "帳號/儲存庫名稱"
    branch = "main"

發佈流程（publish）：
    1. 從 GitHub 抓最新的 .db 和它的版本號（sha）
    2. 在複本上合併新資料
    3. 帶著版本號寫回 GitHub
    4. 如果這段期間有人先發佈了（版本號對不上），GitHub 會拒絕 → 從 1 重來
這樣多人同時登錄也不會互相蓋掉。
"""
import base64

import requests

API = "https://api.github.com"
TIMEOUT = 60


class ConflictError(Exception):
    """發佈時檔案已被別人更新過。"""


class GitHubStore:
    def __init__(self, token: str, repo: str, branch: str = "main"):
        self.repo = repo
        self.branch = branch
        self.s = requests.Session()
        self.s.headers.update({
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        })

    def _url(self, path: str) -> str:
        return f"{API}/repos/{self.repo}/contents/{path}"

    def fetch(self, path: str) -> tuple[bytes | None, str | None]:
        """回傳 (檔案內容, 版本號)；檔案還不存在時回傳 (None, None)。"""
        meta = self.s.get(self._url(path), params={"ref": self.branch},
                          headers={"Accept": "application/vnd.github+json"}, timeout=TIMEOUT)
        if meta.status_code == 404:
            return None, None
        meta.raise_for_status()
        sha = meta.json()["sha"]

        raw = self.s.get(self._url(path), params={"ref": self.branch},
                         headers={"Accept": "application/vnd.github.raw+json"}, timeout=TIMEOUT)
        raw.raise_for_status()
        return raw.content, sha

    def save(self, path: str, content: bytes, sha: str | None, message: str) -> None:
        body = {
            "message": message,
            "content": base64.b64encode(content).decode("ascii"),
            "branch": self.branch,
        }
        if sha:
            body["sha"] = sha
        r = self.s.put(self._url(path), json=body, timeout=TIMEOUT)
        if r.status_code in (409, 422):
            raise ConflictError(r.text)
        r.raise_for_status()

    def publish(self, path: str, merge_fn, message: str, retries: int = 3):
        """
        merge_fn(舊的 db bytes 或 None) → (新的 db bytes, 其他結果…)
        成功時回傳 merge_fn 的結果。
        """
        last_error = None
        for _ in range(retries):
            current, sha = self.fetch(path)
            result = merge_fn(current)
            try:
                self.save(path, result[0], sha, message)
                return result
            except ConflictError as e:
                last_error = e
                continue
        raise RuntimeError(f"有其他人同時在發佈，重試 {retries} 次仍失敗，請稍後再試。（{last_error}）")
