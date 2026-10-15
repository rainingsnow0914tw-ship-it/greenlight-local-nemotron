"""把本機的專案搬上雲：媒體檔進 GCS、狀態進 Firestore。

為什麼需要這支：
    開發期所有東西都在 `~/.greenlight/`（專案 JSON + 影片圖片）。
    Cloud Run 的容器磁碟是暫時的、執行個體之間也不共用，
    所以正式環境必須 Firestore + GCS。

    但第 1 章那 12 支影片、成片、三語字幕、選角候選圖，
    是真金白銀跑出來的（$5.4）—— 不能重跑，只能搬。

搬的順序不能顛倒：
    先傳媒體拿到 gs:// URI → 改寫狀態裡的 URI → 才寫 Firestore。
    反過來的話 Firestore 裡會有一份指向本機路徑的狀態，
    而它在雲端**讀不到檔案但不會報錯**（media_store.load 回 None，
    UI 顯示「檔案遺失」）—— 那是最難查的一種壞法。

不刪本機資料：
    搬完之後本機那份照樣留著。開發時 GREENLIGHT_STORE 沒設就走本機，
    兩邊互不干擾。刪除是不可逆的，而這支腳本可能會被重跑。

用法：
    ./.venv/Scripts/python.exe scripts/probes/migrate_to_cloud.py shensi-001
    ./.venv/Scripts/python.exe scripts/probes/migrate_to_cloud.py shensi-001 --dry-run
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # scripts/probes/ 往上兩層才是 repo 根
sys.path.insert(0, str(ROOT / "backend"))

from app.services import env_boot, media_store, project_store  # noqa: E402

env_boot.load()

_LOCAL_SCHEME = "local://"


def collect_uris(obj, found: set[str]) -> None:
    """把狀態裡所有 `local://` URI 挖出來。

    走整棵 JSON 而不是列舉已知欄位 —— URI 散在 clips[].uri、
    test_shot.uri、casting.sessions[].rounds[].candidates[].image_url、
    segment.output_uri…… 列舉一定會漏，而漏掉的那個不會報錯。
    """
    if isinstance(obj, str):
        if obj.startswith(_LOCAL_SCHEME):
            found.add(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            collect_uris(v, found)
    elif isinstance(obj, list):
        for v in obj:
            collect_uris(v, found)


def rewrite(obj, mapping: dict[str, str]):
    """把狀態裡的 local:// URI 換成 gs://。回傳新的結構。"""
    if isinstance(obj, str):
        return mapping.get(obj, obj)
    if isinstance(obj, dict):
        return {k: rewrite(v, mapping) for k, v in obj.items()}
    if isinstance(obj, list):
        return [rewrite(v, mapping) for v in obj]
    return obj


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("project_id")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    bucket = os.getenv("GCS_BUCKET", "").strip()
    if not bucket:
        print("X 沒有設 GCS_BUCKET。在 .env 加上要搬去哪個 bucket。")
        return 1

    # 一定要從**本機**讀，不管 .env 的 GREENLIGHT_STORE 設成什麼。
    #    設成 firestore 的時候從 Firestore 讀一份空的再寫回去 = 什麼都沒搬，
    #    而且看起來會成功。
    local = project_store.LocalJsonStore(Path.home() / ".greenlight" / "projects")
    state = local.load(args.project_id)
    if state is None:
        print(f"X 本機找不到專案 {args.project_id}")
        return 1

    data = state.model_dump(mode="json")
    uris: set[str] = set()
    collect_uris(data, uris)

    print(f"專案 {args.project_id}")
    print(f"  目標 bucket : gs://{bucket}")
    print(f"  要搬的媒體  : {len(uris)} 個")

    total_bytes = 0
    missing = []
    for uri in sorted(uris):
        path = media_store.path_for(uri)
        if path and path.exists():
            total_bytes += path.stat().st_size
        else:
            missing.append(uri)
    print(f"  總大小      : {total_bytes / 1024 / 1024:.1f} MB")
    if missing:
        print(f"  ⚠️ 本機找不到 {len(missing)} 個（會保持原樣，不會憑空生出來）：")
        for uri in missing[:5]:
            print(f"       {uri}")

    if args.dry_run:
        print()
        print("（乾跑，什麼都沒做）")
        return 0

    print()
    from google.cloud import storage

    client = storage.Client(project=os.getenv("GCP_PROJECT_ID"))
    gcs_bucket = client.bucket(bucket)

    mapping: dict[str, str] = {}
    for i, uri in enumerate(sorted(uris), 1):
        path = media_store.path_for(uri)
        if not path or not path.exists():
            continue
        rel = uri[len(_LOCAL_SCHEME) :]
        blob = gcs_bucket.blob(rel)
        # 已經傳過就跳過 —— 這支可能被重跑，重傳 100MB 沒有意義。
        if blob.exists():
            print(f"  跳過 {i}/{len(uris)} {rel}（雲端已有）")
        else:
            ext = rel.rsplit(".", 1)[-1].lower()
            mime = next(
                (m for m, e in media_store._EXT_BY_MIME.items() if e == ext),  # noqa: SLF001
                "application/octet-stream",
            )
            blob.upload_from_filename(str(path), content_type=mime)
            size = path.stat().st_size / 1024 / 1024
            print(f"  OK   {i}/{len(uris)} {rel}  {size:.1f} MB")
        mapping[uri] = f"gs://{bucket}/{rel}"

    print()
    print(f"媒體搬完：{len(mapping)}/{len(uris)}")

    # ---- 改寫 URI 再寫 Firestore ----
    from app.schemas.project import ProjectState

    migrated = ProjectState.model_validate(rewrite(data, mapping))

    os.environ["GREENLIGHT_STORE"] = "firestore"
    project_store.set_store(project_store.FirestoreStore())
    project_store.get_store().save(migrated)

    remaining: set[str] = set()
    collect_uris(migrated.model_dump(mode="json"), remaining)
    print(f"狀態已寫入 Firestore（還有 {len(remaining)} 個 local:// URI 沒換掉）")
    if remaining:
        print("  ⚠️ 那些是本機找不到檔案的，雲端會顯示「檔案遺失」")

    print()
    print("[注意] 本機那份沒有動。開發時 GREENLIGHT_STORE 沒設就照樣走本機。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
