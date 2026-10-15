"""專案狀態的持久化：讓連續任務 hold 得住。

設計理由：
    使用者關掉瀏覽器、隔天回來，必須能接上。抽角色可能來回十輪、
    3 分鐘的片跑一次半小時，沒有持久狀態這產品根本不能用。

儲存後端的選擇：

    ClickHouse 不採用：它是 OLAP，擅長「在幾十萬筆裡聚合」，
       不擅長「頻繁 update 單一文件」。用它存狀態是誤用。
       ClickHouse 在本專案的角色是生成紀錄的統計，不是狀態儲存。

    本機 JSON（開發期）：零成本、零依賴。

    Firestore（正式環境）：免費額度每天 5 萬讀 / 2 萬寫，
       這個規模的專案綽綽有餘；原生支援單文件讀寫；Cloud Run 無狀態也沒問題。

    設計成可抽換：ProjectStore 是介面，實作可以換而不動狀態機。

單一文件包含全部狀態，不拆表。
   存取模式是「一次讀全部、改一小塊、寫回去」，不是關聯查詢。
   一份 JSON 最簡單、最不會出錯，也最容易搬家。

   注意大小：一份 90 頁劇本 + 60 鏡頭 + 5 角色 × 10 輪候選約 300–500 KB。
   Firestore 單文件上限 1 MB，劇本原文很長時應該存 GCS，這裡只放 URI。
"""

from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod
from pathlib import Path

from ..schemas.project import ProjectState

logger = logging.getLogger(__name__)


class ProjectStore(ABC):
    """專案狀態儲存介面。"""

    @abstractmethod
    def save(self, state: ProjectState) -> None: ...

    def mutate(self, project_id: str, fn) -> "ProjectState": ...

    @abstractmethod
    def load(self, project_id: str) -> ProjectState | None: ...

    @abstractmethod
    def list_ids(self) -> list[str]: ...

    @abstractmethod
    def delete(self, project_id: str) -> bool: ...


# 本機 mutate 用的 process 鎖（LocalJsonStore 是單進程開發用，這樣就夠）
import threading

_LOCAL_MUTATE_LOCK = threading.Lock()


class LocalJsonStore(ProjectStore):
    """本機 JSON 檔。開發期用，零成本零依賴。

    每個專案一個檔：`<root>/<project_id>.json`

    寫入用「先寫暫存檔再改名」的方式 ——
    直接覆寫的話，如果程式在寫到一半掛掉，會留下一個半截的 JSON，
    那比沒有檔案更糟（載入時才發現壞掉，而且不知道壞在哪）。
    改名在同一個檔案系統上是原子操作，不會有半截狀態。
    """

    def __init__(self, root: Path | str | None = None):
        from .env_boot import data_home
        default_root = data_home() / "projects"
        self.root = Path(root) if root else default_root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, project_id: str) -> Path:
        # 防止 project_id 帶路徑分隔符跳出目錄
        safe = project_id.replace("/", "_").replace("\\", "_").replace("..", "_")
        return self.root / f"{safe}.json"

    def save(self, state: ProjectState) -> None:
        state.touch()
        path = self._path(state.project_id)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            state.model_dump_json(indent=2, exclude_none=False), encoding="utf-8"
        )
        tmp.replace(path)  # 原子操作

    def load(self, project_id: str) -> ProjectState | None:
        path = self._path(project_id)
        if not path.exists():
            return None
        try:
            return ProjectState.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            # 載入失敗不要靜默回 None，那會讓使用者以為專案不見了。
            # 記 error 並拋出，讓呼叫端能顯示「這個專案的存檔損壞」。
            logger.error("專案 %s 的存檔無法解析：%s", project_id, exc)
            raise

    def list_ids(self) -> list[str]:
        return sorted(p.stem for p in self.root.glob("*.json"))

    def delete(self, project_id: str) -> bool:
        path = self._path(project_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    def mutate(self, project_id: str, fn) -> ProjectState:
        """交易式讀改寫。本機用 process 鎖就夠（開發期單進程）。"""
        with _LOCAL_MUTATE_LOCK:
            state = self.load(project_id)
            if state is None:
                raise KeyError(f"專案 {project_id} 不存在")
            result = fn(state)
            state = result if result is not None else state
            self.save(state)
            return state


class FirestoreStore(ProjectStore):
    """Firestore。正式環境用。

    集合結構：`greenlight_projects/{project_id}`

    開發期 LocalJsonStore 已滿足需求，只在 Cloud Run 部署時才會用到這個後端。
    """

    # Demo 模式用不同 collection：訪客怎麼點都碰不到正式資料。
    # 共用同一個 GCS bucket 是安全的，demo 不生成新檔案，只讀已有的。
    @property
    def COLLECTION(self) -> str:  # noqa: N802 - 沿用原本的名字，呼叫端不用改
        from . import demo_mode

        return "greenlight_projects" + demo_mode.collection_suffix()

    def __init__(self, project_id: str | None = None):
        from google.cloud import firestore  # 延遲 import，開發期不需要這個依賴

        self._db = firestore.Client(project=project_id or os.getenv("GCP_PROJECT_ID"))

    def save(self, state: ProjectState) -> None:
        state.touch()
        self._db.collection(self.COLLECTION).document(state.project_id).set(
            json.loads(state.model_dump_json())
        )

    def load(self, project_id: str) -> ProjectState | None:
        doc = self._db.collection(self.COLLECTION).document(project_id).get()
        if not doc.exists:
            return None
        return ProjectState.model_validate(doc.to_dict())

    def list_ids(self) -> list[str]:
        return [d.id for d in self._db.collection(self.COLLECTION).stream()]

    def delete(self, project_id: str) -> bool:
        self._db.collection(self.COLLECTION).document(project_id).delete()
        return True

    def mutate(self, project_id: str, fn) -> ProjectState:
        """交易式讀改寫（Firestore transaction）。

        為什麼必須有這個：
           多個角色同時按「生成候選」時，多個請求各自 load 整份狀態、
           改自己的 session、save 整份，最後存的會把前面的全蓋掉（lost update）：
           錢花了、圖生了、候選紀錄消失。

           Firestore transaction 在提交時檢查文件有沒有被別人改過，
           被改過就自動拿新資料重跑 fn，每個請求的改動都疊在
           最新狀態上，誰都不會蓋掉誰。

        fn 必須快且冪等（transaction 衝突時會被重跑）。
           生圖、呼叫模型這種又貴又慢的事一律在 mutate 之外先做完，
           fn 只負責把結果掛進最新狀態。
        """
        from google.cloud import firestore

        ref = self._db.collection(self.COLLECTION).document(project_id)

        @firestore.transactional
        def _tx(tx) -> ProjectState:
            snap = ref.get(transaction=tx)
            if not snap.exists:
                raise KeyError(f"專案 {project_id} 不存在")
            state = ProjectState.model_validate(snap.to_dict())
            result = fn(state)
            state = result if result is not None else state
            state.touch()
            tx.set(ref, json.loads(state.model_dump_json()))
            return state

        return _tx(self._db.transaction())


_store: ProjectStore | None = None


def get_store() -> ProjectStore:
    """取得目前設定的儲存後端。

    預設本機 JSON。設 `GREENLIGHT_STORE=firestore` 才走 Firestore ——
    預設走雲端的話，開發時每存一次檔都在動用配額，沒有必要。
    """
    global _store
    if _store is not None:
        return _store

    backend = os.getenv("GREENLIGHT_STORE", "local").lower()
    if backend == "firestore":
        _store = FirestoreStore()
        logger.info("專案儲存：Firestore")
    else:
        root = os.getenv("GREENLIGHT_STORE_PATH")
        _store = LocalJsonStore(root)
        logger.info("專案儲存：本機 JSON（%s）", _store.root)  # type: ignore[attr-defined]

    return _store


def set_store(store: ProjectStore) -> None:
    """換掉儲存後端。測試用。"""
    global _store
    _store = store
