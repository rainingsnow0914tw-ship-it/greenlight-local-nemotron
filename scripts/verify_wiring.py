"""接線驗證 —— **$0，不呼叫任何 AI API，不生成任何影片。**

═══════════════════════════════════════════════════════════════════════════
為什麼需要這支（先測試通不通，驗證程式碼有沒有問題）
═══════════════════════════════════════════════════════════════════════════

同一個專案裡抓到過**五個同構的問題**：

    ledger.record_generation()      只有模擬資料腳本呼叫過
    ledger.make_history_lookup()    零呼叫者
    SegmentStatus 的 6 個狀態       只有 1 個被賦值過
    ConstraintPlan.skipped          註釋說要顯示，route 與前端零引用
    rewind_to / blockers_for        零 route 呼叫者

全部都「有 code、有測試、有註釋、文件寫完成」。
**模組品質跟模組有沒有接上，是兩件獨立的事** —— 而前者做得越好，
越沒有人會去懷疑後者。

修完之後又多了一批沒跑過的新路徑（_flush_ledger / _capture_facts /
memory_block 注入），所以這支存在的意義就是：**別再犯同一個錯**。

═══════════════════════════════════════════════════════════════════════════
設計原則：除了真正付錢的那一步之外，全部跑真的
═══════════════════════════════════════════════════════════════════════════

用 TestClient 打**真實 route**，只把會扣款的三個外部呼叫換成樁：

    video_gen.generate_clip / extend_clip   Veo（$0.20–0.40 一次）
    assembler.concat                        雖然是 $0，但要真檔案
    voice_profiler.profile_voice            Gemini 聽影片

其餘（route → _capture_facts → 成本計算 → _flush_ledger → ClickHouse
→ 狀態推導）全部走真實程式碼。樁只回傳假的「生成結果」，
不改變任何一行被驗證的邏輯。

隔離（兩層，都必須）：
    專案狀態 → 暫存目錄（GREENLIGHT_STORE_PATH），不碰 ~/.greenlight/projects
    ledger   → 獨立資料庫 greenlight_wiring_test，跑完整個 DROP

    為什麼 ledger 不能只靠「事後刪測試列」：
    constraint_stats 與 calibration_stats 是 SummingMergeTree 聚合表，
    **沒有 plan_id 可以篩**。測試列一旦進去就永久混在真實統計裡。

用法：
    ./.venv/Scripts/python.exe scripts/verify_wiring.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

# 環境隔離必須在 import app 之前設好 —— ledger 的連線是快取的，
#    app 一起來就可能已經連上正式資料庫了。
TEST_DB = "greenlight_wiring_test"
_TMP_STORE = tempfile.mkdtemp(prefix="greenlight_wiring_")
os.environ["GREENLIGHT_STORE_PATH"] = _TMP_STORE
# media 也隔離到暫存目錄，避免污染 ~/.greenlight/media/。
# 收尾 shutil.rmtree(_TMP_STORE) 會一起清掉。
os.environ["GREENLIGHT_MEDIA_ROOT"] = os.path.join(_TMP_STORE, "media")

from app.services import env_boot  # noqa: E402

env_boot.load()
os.environ["CLICKHOUSE_DB"] = TEST_DB  # load() 之後才蓋，否則會被 .env 蓋回去
# verify_wiring 是 $0 本機驗證，不能連 GCS —— 有的話 media_store.save()
#    會走上雲分支需要 ADC，測試環境過不了 reauth。強制走本機。
#    必須在 env_boot.load() 之後 pop，否則會被 .env 蓋回去（同 CLICKHOUSE_DB 的坑）。
os.environ.pop("GCS_BUCKET", None)

from fastapi.testclient import TestClient  # noqa: E402

from app.agents import voice_profiler  # noqa: E402
from app.main import app  # noqa: E402
from app.schemas.project import Stage  # noqa: E402
from app.schemas.segment import ClipStatus, SegmentStatus  # noqa: E402
from app.services import (  # noqa: E402
    assembler,
    ledger,
    media_store,
    project_store,
    refinery,
    video_gen,
)

TEST_PROJECT = "wiringtest"
SOURCE_PROJECT = "shensi-001"

_passed: list[str] = []
_failed: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    (_passed if ok else _failed).append(name)
    print(f"  {'OK ' if ok else 'X  '}{name}{'  — ' + detail if detail else ''}")
    return ok


def detail_of(response) -> str:
    """把 API 的實際回應擠成一行。

    失敗的檢查一定要說「實際發生什麼」，不能只回一個狀態碼。
       這整個專案今天抓到的每一個 bug，難查的原因都是同一個：
       它報的訊息不足以判斷該往哪裡看。測試自己不能犯一樣的毛病。
    """
    try:
        body = response.json()
        msg = body.get("detail") or str(body)[:120]
    except Exception:  # noqa: BLE001
        msg = response.text[:120]
    return f"{response.status_code} {msg}"


# ---------------------------------------------------------------------------
# 樁 —— 只換會花錢的三個
# ---------------------------------------------------------------------------

_gen_calls: list[dict] = []
_fail_next = {"on": False}


def _stub_generate_clip(project_id, shot_id, prompt, **kw):
    """假裝 Veo 生成了一段影片。**不呼叫任何 API、不寫任何檔案。**"""
    _gen_calls.append({"shot_id": shot_id, "prompt": prompt, **kw})
    if _fail_next["on"]:
        _fail_next["on"] = False
        raise video_gen.VideoGenError("測試用的假失敗")
    seconds = float(kw.get("duration_seconds", 8))
    return video_gen.ClipResult(
        uri=f"local://{project_id}/{shot_id}.mp4",
        seconds=seconds,
        width=1280,
        height=720,
        size_bytes=1024,
        requested_seconds=int(seconds),
        tier=kw.get("tier", "lite_720p"),
        model_id="veo-3.1-lite-generate-001",
    )


def _stub_extend_clip(project_id, shot_id, source, prompt, **kw):
    return video_gen.ClipResult(
        uri=f"local://{project_id}/{shot_id}.mp4",
        seconds=15.0,
        width=1280,
        height=720,
        size_bytes=2048,
        tier=kw.get("tier", "lite_720p"),
        model_id="veo-3.1-lite-generate-001",
    )


def _stub_concat(project_id, clips, name):
    return f"local://{project_id}/{name}.mp4", sum(c.seconds for c in clips)


def _stub_concat_sources(project_id, sources, name):
    """全片合成的樁。

    為什麼也要樁：這一支會真的呼叫 ffmpeg 讀檔案，而測試環境裡的
       「成片」是幾百 bytes 的假 mp4 —— ffmpeg 會回 `Error opening input`。
       真正的 ffmpeg 行為（能不能接、要跑多久）用真實檔案在雲端量，
       這裡驗的是接線：route 有沒有把段落挑對、狀態有沒有存回去。
    """
    total = sum(seconds for _uri, seconds, _label in sources)
    # 真的寫一個檔案出去 —— 後面的 download / burn 要讀得到它。
    from app.services import media_store as _ms

    uri = _ms.save(project_id, name, b"FILM" + b"0" * 128, "video/mp4")
    return uri, total


def _stub_profile_voice(*a, **kw):
    raise AssertionError("音色分析不該在這個測試裡被呼叫（它會花錢）")


def install_stubs() -> None:
    video_gen.generate_clip = _stub_generate_clip
    video_gen.extend_clip = _stub_extend_clip
    assembler.concat = _stub_concat
    assembler.concat_sources = _stub_concat_sources
    voice_profiler.profile_voice = _stub_profile_voice


# ---------------------------------------------------------------------------
# 前置
# ---------------------------------------------------------------------------

def _bootstrap_client():
    """連到 `default` —— 用來建立與刪除測試資料庫本身。

    不能連 TEST_DB：那個資料庫正是我們要建的，而 clickhouse-connect
       在連線階段就會驗證資料庫存不存在。
    """
    saved = os.environ.get("CLICKHOUSE_DB")
    os.environ["CLICKHOUSE_DB"] = "default"
    ledger.reset_connection()
    client = ledger._get_client()  # noqa: SLF001
    if saved is not None:
        os.environ["CLICKHOUSE_DB"] = saved
    return client


def setup_test_db() -> bool:
    """在獨立資料庫裡建一份 schema。

    走**跟應用程式相同的連線路徑**，不是 docker exec。
       原本用 docker exec，那只在「ClickHouse 跑在本機容器」時成立 ——
       接上 ClickHouse Cloud 之後根本沒有容器可以 exec，測試整支就啟動不了。

       這正是 setup_clickhouse.py 註釋裡寫的那條：
       **schema 只要有兩份施加方式，遲早會有一邊漏掉。**
       那支寫完這句話，這支自己就犯了。
    """
    if ledger._local_mode():  # noqa: SLF001
        # Greenlight Local：學習紀錄是使用者電腦裡的 SQLite。測試就把資料家目錄指到暫存資料夾，
        #    跑完整個刪掉 —— 不碰使用者真的紀錄，也不需要建／刪資料庫。
        global _LOCAL_TEST_HOME
        _LOCAL_TEST_HOME = tempfile.mkdtemp(prefix="greenlight_ledger_")
        os.environ["GREENLIGHT_HOME"] = _LOCAL_TEST_HOME
        # 這支驗的是雲端流程（Veo／Omni 用樁）：明講用雲端引擎。
        #    Greenlight Local 的預設是本機引擎，不設的話試拍會走本機、要求先跑導演迴圈。
        from app.services import settings_store

        cfg = settings_store.load()
        cfg["engine"] = "cloud"
        settings_store.save(cfg)
        ledger.reset_connection()
        return ledger._get_client() is not None  # noqa: SLF001

    schema = (ROOT / "deployment" / "clickhouse" / "schema.sql").read_text(
        encoding="utf-8"
    )
    # 剝掉 `--` 註解再切分號 —— 註解裡有分號的話直接切會把語句劈成兩半
    body = "\n".join(
        ln for ln in schema.splitlines() if not ln.strip().startswith("--")
    )
    statements = [
        s.strip().replace("greenlight.", f"{TEST_DB}.").replace(
            "CREATE DATABASE IF NOT EXISTS greenlight",
            f"CREATE DATABASE IF NOT EXISTS {TEST_DB}",
        )
        for s in body.split(";")
        if s.strip()
    ]

    client = _bootstrap_client()
    if client is None:
        print("  ! 連不上 ClickHouse，測試無法進行（檢查 .env 的 CLICKHOUSE_*）")
        return False

    try:
        # 先砍掉殘骸再建。上一輪如果中途 crash 或清理失敗，
        #    留下來的舊資料會讓「測試庫是空的」永遠不成立 ——
        #    測試就從那次起再也跑不起來，而原因看起來像產品壞了。
        #    測試必須能自己從殘骸中恢復。
        client.command(f"DROP DATABASE IF EXISTS {TEST_DB}")
        for stmt in statements:
            client.command(stmt)
    except Exception as exc:  # noqa: BLE001
        print(f"  ! 測試資料庫建不起來：{str(exc).splitlines()[0][:160]}")
        return False

    ledger.reset_connection()  # 切回 TEST_DB
    return True


_LOCAL_TEST_HOME = None


def drop_test_db() -> None:
    """刪掉測試資料庫。（本機模式：刪掉暫存的資料家目錄。）"""
    if ledger._local_mode():  # noqa: SLF001
        if _LOCAL_TEST_HOME:
            ledger.reset_connection()
            shutil.rmtree(_LOCAL_TEST_HOME, ignore_errors=True)
        return
    _drop_clickhouse_test_db()


def _drop_clickhouse_test_db() -> None:
    """刪掉測試資料庫。

    失敗一定要說出來。原本這裡整段吞例外，
       結果連線設定被前面的測試弄壞之後，測試資料庫永遠留在 ClickHouse Cloud
       上，而畫面顯示「60 項全過」—— 看起來完美，實際留了垃圾。

       清理失敗是最不容易被發現的失敗：它不影響任何一項檢查的結果。
    """
    try:
        client = _bootstrap_client()
        if client is None:
            print(f"\n⚠️ 清理失敗：連不上 ClickHouse。請手動刪掉資料庫 {TEST_DB}")
            return
        client.command(f"DROP DATABASE IF EXISTS {TEST_DB}")
        remaining = [
            r[0] for r in client.query("SHOW DATABASES").result_rows
        ]
        if TEST_DB in remaining:
            print(f"\n⚠️ 清理失敗：{TEST_DB} 仍然存在，請手動刪掉")
    except Exception as exc:  # noqa: BLE001
        print(f"\n⚠️ 清理失敗（{type(exc).__name__}）：請手動刪掉資料庫 {TEST_DB}")
        print(f"   {str(exc).splitlines()[0][:140]}")
    finally:
        ledger.reset_connection()


def build_fixture() -> bool:
    """從真實專案複製一份當測試素材。

    不手工造 ShotList —— 手工造的資料只會測到「我以為的形狀」。
       真實的鏡頭表帶著 content_flags、first_frame_from、台詞、
       非人角色，那些才是會出事的地方。
    """
    real = project_store.LocalJsonStore(Path.home() / ".greenlight" / "projects")
    state = real.load(SOURCE_PROJECT)
    if state is None:
        print(f"  ! 找不到來源專案 {SOURCE_PROJECT}，無法建立測試素材")
        return False

    state.project_id = TEST_PROJECT
    state.title = "接線驗證用（可刪）"

    # 把選角補滿。
    #    來源專案 shensi-001 只鎖了 2/6 個角色，而選角閘門接上之後
    #    **所有生成路徑都會被 409 擋住** —— 後面二十幾項測試會連鎖失敗，
    #    而失敗訊息長得像「產品壞了」，其實只是素材沒準備好。
    #    第 1a 組會自己解除一個鎖來驗閘門，驗完再還原。
    if state.casting:
        for session in state.casting.sessions:
            if session.selected_candidate_id is None:
                existing = session.all_candidates()  # property 是 method
                session.selected_candidate_id = (
                    existing[0].candidate_id
                    if existing
                    else f"_fixture_{session.character_name}"
                )
    # 只留第一段，且把進度清乾淨 —— 要從頭跑一次
    state.segments = state.segments[:1]
    seg = state.segments[0]
    seg.clips = []
    seg.output_uri = None
    seg.output_seconds = 0.0
    seg.subtitles = {}
    seg.spent_usd = 0.0
    seg.test_shot = None
    seg.test_shot_approved = False
    seg.prompts_exported_at = None

    project_store.get_store().save(state)
    return True


def reload_segment():
    state = project_store.get_store().load(TEST_PROJECT)
    return state, state.segments[0]


# ---------------------------------------------------------------------------
# 檢查
# ---------------------------------------------------------------------------

def ledger_count(outcome: str | None = None) -> int:
    client = ledger._get_client()  # noqa: SLF001
    if client is None:
        return -1
    where = f"WHERE outcome = '{outcome}'" if outcome else ""
    rows = client.query(
        f"SELECT count() FROM {ledger._table()} {where}"  # noqa: SLF001
    ).result_rows
    return int(rows[0][0]) if rows else 0


def main() -> int:
    print("=" * 72)
    print("接線驗證 —— $0，不呼叫任何 AI API")
    print("=" * 72)
    print(f"專案狀態暫存：{_TMP_STORE}")
    print(f"ledger 測試庫：{TEST_DB}")
    print()

    print("[前置]")
    if not setup_test_db():
        return 1
    ledger.reset_connection()
    if not check("ClickHouse 連得上測試庫", ledger.is_available()):
        return 1
    if not check("ledger 測試庫是空的", ledger_count() == 0):
        return 1
    if not build_fixture():
        return 1
    check("測試素材建好", True)
    install_stubs()
    check("樁裝好（Veo / 接片 / 音色分析都不會真的被呼叫）", True)
    print()

    # ── Demo 模式的守衛不能被籠統的 except 吃掉 ─────────────────────
    #
    # 為什麼要靜態檢查：
    #    demo 模式靠 `demo_mode.guard()` 拋 `DemoBlocked` 擋住花錢操作，
    #    但 route 裡有十幾個 `except Exception` —— 其中一個把它吃掉、
    #    轉成 502，於是 demo 站上「內容預審」不是回說明卡而是報錯。
    #
    #    試過讓 DemoBlocked 繼承 BaseException 來一次解決（像 SystemExit
    #    那樣不被 `except Exception` 抓到）——**FastAPI 接不住**，回 500。
    #    所以只能逐處放行，而逐處放行的東西一定要有檢查，
    #    否則下一個新增 route 的人就漏了，而漏掉是靜默的。
    import re as _re

    routes_dir = Path(__file__).resolve().parents[1] / "backend" / "app" / "routes"
    leaky: list[str] = []
    for path in sorted(routes_dir.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        broad = len(_re.findall(r"^\s*except Exception as ", text, _re.M))
        passthrough = len(_re.findall(r"^\s*except demo_mode\.DemoBlocked:", text, _re.M))
        if broad > passthrough:
            leaky.append(f"{path.name}（{broad} 個籠統 except、只放行 {passthrough} 個）")
    check(
        "🔴 每個 route 的籠統 except 都放行 DemoBlocked（漏了 demo 站會回 502 而不是說明）",
        not leaky,
        "、".join(leaky) if leaky else "全部放行",
    )
    print()

    client = TestClient(app)
    base = f"/api/projects/{TEST_PROJECT}"

    # ── 1. 狀態推導 ────────────────────────────────────────────────
    print("[1] 段落狀態是從事實推導的")
    _, seg = reload_segment()
    check(
        "有 shot_list、沒進度 → 已分鏡",
        seg.status is SegmentStatus.SHOTS_PLANNED,
        seg.status.value,
    )

    # ── 1b. 試拍閘門 ───────────────────────────────────────────────
    print()
    print("[1a] 選角閘門 —— 沒選完角不能生成任何東西")
    # 實際走 UI 抓到：CastingGate.can_proceed 存在、README 與
    #    文件都寫「hard gate」，但**沒有任何 route 問過它** —— 試拍按得下去。
    #    這一項守著那個洞。
    state_c, seg_c = reload_segment()
    saved_casting = state_c.casting
    if saved_casting is not None and saved_casting.sessions:
        # 暫時解除一個角色的鎖定，讓閘門變成「沒過」
        victim = saved_casting.sessions[0]
        saved_choice = victim.selected_candidate_id
        victim.selected_candidate_id = None
        project_store.get_store().save(state_c)

        r = client.post(f"{base}/segments/1/test-shot")
        check(
            "🔴 沒選完角就試拍 → 擋下來（409）",
            r.status_code == 409,
            detail_of(r)[:70],
        )
        r = client.post(f"{base}/segments/1/batch/next")
        check(
            "🔴 沒選完角就批次 → 擋下來（409）",
            r.status_code == 409,
            detail_of(r)[:70],
        )
        r = client.get(f"{base}/segments/1/test-shot")
        check(
            "  API 在按下去之前就講明白（casting_ready=false）",
            r.status_code == 200 and r.json().get("casting_ready") is False,
            f"pending={r.json().get('pending_characters')}" if r.status_code == 200 else detail_of(r),
        )

        # 還原
        state_c, _ = reload_segment()
        state_c.casting.sessions[0].selected_candidate_id = saved_choice
        project_store.get_store().save(state_c)
        r = client.get(f"{base}/segments/1/test-shot")
        check(
            "  還原之後閘門放行",
            r.status_code == 200 and r.json().get("casting_ready") is True,
        )
    else:
        check("測試素材沒有選角資料，跳過選角閘門檢查", True, "（來源專案沒有 casting）")

    print()
    print("[1b] 試拍閘門 —— 沒試拍不能批次")
    r = client.post(f"{base}/segments/1/batch/next")
    check(
        "🔴 沒試拍就批次 → 擋下來（409）",
        r.status_code == 409,
        r.json().get("detail", "")[:50] if r.status_code == 409 else str(r.status_code),
    )

    r = client.post(f"{base}/segments/1/test-shot")
    check("試拍 API 回 200", r.status_code == 200, detail_of(r))
    _, seg = reload_segment()
    check("試拍結果存下來了", seg.test_shot is not None)
    # 試拍不算「批次生成中」—— 它是第 7 關，批次是第 8 關。
    # 狀態機刻意只看 clips，所以這時仍是已分鏡。
    check(
        "試拍不會讓狀態跳到生成中（試拍是另一關）",
        seg.status is SegmentStatus.SHOTS_PLANNED,
        seg.status.value,
    )
    r = client.post(f"{base}/segments/1/test-shot/approve")
    check("試拍通過 API 回 200", r.status_code == 200, detail_of(r))
    # 試拍是真實生成、也有人裁決，該算一筆。
    #    這一項原本是「已知缺口」，現在反過來守著它不要再掉。
    check(
        "🔴 試拍通過寫進 ledger（第一個花錢的動作不能漏）",
        ledger_count("accepted") == 1,
        f"accepted {ledger_count('accepted')} 筆",
    )
    _, seg = reload_segment()
    check(
        "  pending 已清空（不會重複寫）",
        seg.test_shot is not None and seg.test_shot.pending_ledger is None,
    )

    # ── 2. 生成一鏡（不裁決）────────────────────────────────────────
    print()
    print("[2] 生成一鏡 —— 事實先扣著，還不進 ledger")
    # 記基準再比差值，不要寫死筆數。
    #    這一段本來寫「ledger 應該是 0 筆」，把試拍也接進 ledger
    #    之後就壞了 —— **而壞的樣子看起來像產品出問題**，不像測試過期。
    #    這正是本檔開頭寫過的那條：斷言語意，不要斷言數字。
    ledger_before_gen = ledger_count()
    r = client.post(f"{base}/segments/1/batch/next")
    if not check("批次生成 API 回 200", r.status_code == 200, detail_of(r)):
        return 1

    _, seg = reload_segment()
    # 抓「這次生的那一鏡」＝ 唯一在等確認的，不要用 clips[0]。
    #    試拍改成挑第一鏡之後，clips[0] 是試拍那鏡（已 approved），
    #    不是這次批次生的。**斷言綁在陣列位置就會這樣壞**，
    #    而壞掉的訊息看起來像產品出問題。
    clip = next(
        (c for c in seg.clips if c.status is ClipStatus.AWAITING_APPROVAL),
        seg.clips[0],
    )
    check("clip 生出來了", bool(clip.uri), f"{clip.shot_id} · {clip.uri}")
    check("段落狀態 → 生成中", seg.status is SegmentStatus.GENERATING, seg.status.value)
    check("pending_ledger 有值（事實扣著）", clip.pending_ledger is not None)
    if clip.pending_ledger:
        f = clip.pending_ledger
        check("  抓到預測成功率", f.predicted_success_rate > 0, f"{f.predicted_success_rate:.0%}")
        check("  抓到套用的約束", len(f.applied_constraints) > 0, f"{len(f.applied_constraints)} 條")
        check("  抓到 model_id", bool(f.model_id), f.model_id)
        check("  抓到本次花費", f.cost_usd > 0, f"${f.cost_usd}")
    check(
        "🔴 還沒裁決 → ledger 沒有增加",
        ledger_count() == ledger_before_gen,
        f"生成前 {ledger_before_gen} 筆、生成後 {ledger_count()} 筆",
    )

    # ── 3. 通過 ────────────────────────────────────────────────────
    print()
    print("[3] 按通過 → 落一筆 accepted")
    accepted_before = ledger_count("accepted")
    r = client.post(f"{base}/segments/1/batch/{clip.shot_id}/approve")
    check("approve API 回 200", r.status_code == 200, detail_of(r))
    check(
        "ledger 多一筆 accepted",
        ledger_count("accepted") == accepted_before + 1,
        f"{accepted_before} → {ledger_count('accepted')} 筆",
    )
    _, seg = reload_segment()
    check(
        "pending_ledger 已清空（不會重複寫）",
        seg.clips[0].pending_ledger is None,
    )

    # ── 4. 重拍 ────────────────────────────────────────────────────
    print()
    print("[4] 按重拍 → 上一次落成 rejected，然後重生")
    rejected_before = ledger_count("rejected")
    r = client.post(f"{base}/segments/1/batch/next")  # 再生一鏡
    check("再生一鏡", r.status_code == 200, detail_of(r))
    _, seg = reload_segment()
    # 抓剛生的那一鏡（唯一在等確認的），不要用 clips[1]。
    #    clips[1] 是前面已經 approve 過的那鏡，它的 pending_ledger 早就清空 ——
    #    對它 retry 不會落任何 rejected，而剛生的那鏡會一直卡在等確認，
    #    把後面兩組測試全部連鎖擋掉。
    second = next(
        (c for c in seg.clips if c.status is ClipStatus.AWAITING_APPROVAL),
        seg.clips[-1],
    )
    r = client.post(f"{base}/segments/1/batch/{second.shot_id}/retry")
    check("retry API 回 200", r.status_code == 200, detail_of(r))
    check(
        "ledger 多一筆 rejected",
        ledger_count("rejected") == rejected_before + 1,
        f"{rejected_before} → {ledger_count('rejected')} 筆",
    )
    _, seg = reload_segment()
    # 同上：跟著 shot_id 走，不要跟著陣列位置走。
    retried = next((c for c in seg.clips if c.shot_id == second.shot_id), None)
    check(
        "重拍後 attempts = 2",
        retried is not None and retried.attempts == 2,
        f"{second.shot_id} attempts={retried.attempts if retried else '?'}",
    )
    check("重拍後又有新的 pending", retried is not None and retried.pending_ledger is not None)
    if retried and retried.pending_ledger:
        check("  新的 pending attempt = 2", retried.pending_ledger.attempt == 2)

    # ── 4b. 逐鏡確認閘門 ───────────────────────────────────────────
    print()
    print("[4b] 有鏡頭在等確認時擋住下一次生成")
    r = client.post(f"{base}/segments/1/batch/next")
    check(
        "🔴 上一鏡沒確認就生下一鏡 → 擋下來（409）",
        r.status_code == 409,
        detail_of(r),
    )
    # 把它確認掉，才能繼續測
    r = client.post(f"{base}/segments/1/batch/{second.shot_id}/approve")
    check("確認第二鏡", r.status_code == 200, detail_of(r))

    # ── 5. 生成失敗 ────────────────────────────────────────────────
    print()
    print("[5] 生成失敗 → 落一筆 error")
    _fail_next["on"] = True
    r = client.post(f"{base}/segments/1/batch/next")
    check("失敗時回 502（不是靜默吞掉）", r.status_code == 502, detail_of(r))
    check("ledger 多一筆 error", ledger_count("error") == 1, f"{ledger_count('error')} 筆")
    # 斷言「語意」不是「數字」——寫死筆數的測試在流程一改就會壞，
    #    而且壞的時候看起來像產品出問題。這裡直接問統計本身。
    client_ch = ledger._get_client()  # noqa: SLF001
    pass_rate_rows = client_ch.query(
        f"SELECT count() FROM {ledger._table()} "  # noqa: SLF001
        "WHERE attempt = 1 AND outcome != 'error'"
    ).result_rows
    total_rows = client_ch.query(
        f"SELECT count() FROM {ledger._table()}"  # noqa: SLF001
    ).result_rows
    check(
        "🔴 error 有寫進 ledger，但被排除在通過率統計之外",
        int(pass_rate_rows[0][0]) < int(total_rows[0][0]),
        f"通過率母體 {pass_rate_rows[0][0]} 筆 < 總筆數 {total_rows[0][0]} 筆",
    )
    mv_rows = client_ch.query(
        f"SELECT sum(attempts) FROM {ledger._db()}.calibration_stats"  # noqa: SLF001
    ).result_rows
    check(
        "  物化檢視也沒收 error",
        int(mv_rows[0][0] or 0) == int(pass_rate_rows[0][0]),
        f"MV {mv_rows[0][0]} 筆",
    )
    ledger_before_reset = ledger_count()

    # ── 6. 歷史查詢真的被呼叫 ──────────────────────────────────────
    print()
    print("[6] 規劃時真的去查了歷史")
    # 數的是「整包統計被拉了幾次」，不是「單筆查詢被呼叫幾次」。
    #    這裡壞過一次：原本數 query_similar_shots 的呼叫次數，
    #    而效能優化把它換成一次 GROUP BY 之後，那個計數永遠是 0 ——
    #    測試報「history_lookup 沒被呼叫」，但實際上它被呼叫了、
    #    只是換了條路。**斷言綁在實作細節上就會這樣。**
    #
    #    所以除了計數，下面還加一條語意斷言：規劃結果的信心值必須是
    #    blended（先驗 + 實測混合），那個跟實作怎麼寫完全無關。
    hits = {"n": 0}
    original = ledger._load_style_buckets  # noqa: SLF001

    def counting(*a, **kw):
        hits["n"] += 1
        return original(*a, **kw)

    ledger._load_style_buckets = counting  # noqa: SLF001
    try:
        r = client.get(f"{base}/segments/1/shots")
        check("分鏡頁 API 回 200", r.status_code == 200, detail_of(r))
        check("🔴 history_lookup 真的被呼叫", hits["n"] > 0, f"{hits['n']} 次拉取")
        check(
            "一次拉完，沒有每鏡每檔位各查一次",
            hits["n"] <= 3,
            f"{hits['n']} 次（一個檔位一次，12 鏡 x 3 檔位 = 36 是舊做法的下限）",
        )
        # 這裡斷言的是**誠實原則**，不是「一定要混到實測資料」。
        #    測試資料庫本來就只有兩三筆（乾淨環境），低於 _MIN_SAMPLES_PER_LEVEL，
        #    所以系統該老實說「這是先驗估計」。
        #    如果樣本這麼少還敢回 blended，那才是 bug ——
        #    那代表它拿 2 筆資料當統計用，而使用者會以為那個數字有根據。
        confidences = {
            v["confidence"] for v in r.json().get("variants", []) if v.get("confidence")
        }
        check(
            "🔴 樣本不足時誠實標示為先驗估計",
            confidences == {"prior_only"},
            f"信心值：{', '.join(sorted(confidences)) or '（無）'}",
        )
        data = r.json()
        shot = data["shot_list"]["shots"][0]
        check("約束方案有出到 API", "constraints" in shot)
        if "constraints" in shot:
            c = shot["constraints"]
            check("  有 matched_count", "matched_count" in c, str(c.get("matched_count")))
            capped = [
                s for s in data["shot_list"]["shots"] if s["constraints"]["skipped"]
            ]
            check(
                "  被上限擋掉的約束有出來",
                len(capped) > 0,
                f"{len(capped)}/{len(data['shot_list']['shots'])} 鏡有被擋",
            )
    finally:
        ledger._load_style_buckets = original  # noqa: SLF001

    # ── 7. 接片 + 提煉 ─────────────────────────────────────────────
    print()
    print("[7] 接片 → 狀態變已完成，順便觸發提煉")
    r = client.post(f"{base}/segments/1/assemble")
    check("assemble API 回 200", r.status_code == 200, detail_of(r))
    if r.status_code == 200:
        mem = r.json().get("memory", {})
        check("回應帶了 memory 區塊（不靜默）", "refined" in mem)
        check(
            "🔴 樣本不足時拒絕提煉且花 $0",
            mem.get("refined") is False and mem.get("cost_usd", 0) == 0,
            mem.get("reason", "")[:60],
        )
    _, seg = reload_segment()
    check("段落狀態 → 已完成", seg.status is SegmentStatus.DONE, seg.status.value)

    # ── 8. 重置 ────────────────────────────────────────────────────
    print()
    print("[8] 重置批次 → 成片與字幕一起清掉")
    r = client.delete(f"{base}/segments/1/batch")
    check("reset API 回 200", r.status_code == 200, detail_of(r))
    _, seg = reload_segment()
    check("clips 清空", len(seg.clips) == 0)
    check("🔴 output_uri 也清掉（不留孤兒成片）", not seg.output_uri)
    check("字幕清掉", not seg.subtitles)
    check(
        "狀態退回（沒有人手動改過 status）",
        seg.status is not SegmentStatus.DONE,
        seg.status.value,
    )
    # 重置清的是「這一段的進度」，不是「系統學到的東西」。
    #    學習迴路的價值就在於它跨得過重來 —— 使用者重跑一次，
    #    系統應該比上一次更準，而不是回到什麼都不知道。
    check(
        "🔴 重置不會刪掉已經學到的 ledger 紀錄",
        ledger_count() == ledger_before_reset,
        f"重置前 {ledger_before_reset} 筆、重置後 {ledger_count()} 筆",
    )

    # ── 8b. 對正式資料庫驗混合 ─────────────────────────────────────
    #
    # 上面第 6 組驗的是「樣本不足時誠實說先驗」。反過來那一半 ——
    # 樣本夠時真的會用實測值 —— 測試庫裡驗不到（它本來就是空的）。
    # 所以這裡對**正式資料庫唯讀查一次**，只讀不寫，不留任何痕跡。
    print()
    print("[8b] 樣本夠的時候真的會用實測值（對正式庫唯讀）")
    saved_db = os.environ.get("CLICKHOUSE_DB")
    os.environ["CLICKHOUSE_DB"] = "greenlight"
    ledger.reset_connection()
    try:
        buckets = ledger._load_style_buckets("anime")  # noqa: SLF001
        total = sum(n for n, _ in buckets.values())
        check(
            "正式庫拉得到分組統計",
            total > 0,
            f"{len(buckets)} 組、共 {total} 筆",
        )
        if total >= ledger._MIN_SAMPLES_PER_LEVEL:  # noqa: SLF001
            lookup = ledger.make_history_lookup("anime")
            from app.schemas.plan import QualityTier

            _, fixture_seg = reload_segment()
            rate, samples = lookup(
                fixture_seg.shot_list.shots[0], QualityTier.LITE_720P
            )
            check(
                "🔴 查得到實測通過率（不是 None）",
                rate is not None and samples > 0,
                f"{rate:.0%}（{samples} 筆）" if rate is not None else "None",
            )
        else:
            check(
                "正式庫樣本也不夠，跳過混合驗證",
                True,
                f"{total} 筆 < {ledger._MIN_SAMPLES_PER_LEVEL}",  # noqa: SLF001
            )
    finally:
        if saved_db is not None:
            os.environ["CLICKHOUSE_DB"] = saved_db
        ledger.reset_connection()

    # ── 9. 記憶注入 ────────────────────────────────────────────────
    print()
    print("[9] 跑的時候帶著記憶")
    check("沒有筆記時回空字串（agent 照原本方式跑）", refinery.memory_block("anime") == "")
    notes = refinery.CraftNotes(
        style_id="_wiring_test_style",
        notes="測試用規律：靜態鏡頭比推軌容易過。",
        sample_size=42,
        refined_at="2026-08-08T00:00:00+00:00",
    )
    refinery.save_notes(notes)
    block = refinery.memory_block("_wiring_test_style")
    check("有筆記時組得出記憶區塊", bool(block))
    check("  區塊標明樣本數", "42" in block, "避免 2 筆推出來的規律被當鐵律")
    check("  區塊標明提煉日期", "2026-08-08" in block)
    check("  區塊說明優先級低於物理約束", "物理約束" in block)
    (Path(refinery._notes_dir()) / "_wiring_test_style.json").unlink(missing_ok=True)  # noqa: SLF001

    # ── 10. 降級 ───────────────────────────────────────────────────
    print()
    print("[10] ClickHouse 掛掉時整條路徑不能炸")
    ledger.reset_connection()
    # 把原值存下來再改，還原時放回原值 —— **不要寫死預設值**。
    #    原本 finally 裡硬寫 CLICKHOUSE_PORT="8123"（本機的埠），
    #    接上 ClickHouse Cloud（8443）之後，這一段測完就把連線設定弄壞了，
    #    後面的 drop_test_db() 連不上、例外被吞掉、測試資料庫永遠留在雲端。
    #    清理失敗又剛好是最不會被發現的一種失敗。
    saved_env = {
        k: os.environ.get(k) for k in ("CLICKHOUSE_HOST", "CLICKHOUSE_PORT")
    }
    if ledger._local_mode():  # noqa: SLF001
        # Greenlight Local：沒有雲端資料庫可以「掛掉」；模擬的是本機紀錄檔打不開（磁碟滿、沒權限）。
        #    只讓學習紀錄打不開 —— 不能換整個資料家目錄，那會連設定（引擎、金鑰）一起換掉，測的就不是同一件事了。
        from app.services import local_db

        def _broken(self, path=None):  # noqa: ARG001
            raise OSError("simulated: the learning database cannot be opened")

        _real_init = local_db.LocalClient.__init__
        local_db.LocalClient.__init__ = _broken
    else:
        os.environ["CLICKHOUSE_HOST"] = "127.0.0.1"
        os.environ["CLICKHOUSE_PORT"] = "1"  # 一定連不上的 port
    ledger.reset_connection()
    try:
        check("is_available() 回 False", not ledger.is_available())
        r = client.get(f"{base}/segments/1/shots")
        check("🔴 規劃頁照樣回 200（降級成先驗值）", r.status_code == 200, detail_of(r))
        # 批次重置也會作廢試拍（拿已刪影片放行新批次是漏洞），
        #    前面情境重置過批次，這裡要重走試拍閘門才輪得到生成。
        client.post(f"{base}/segments/1/test-shot")
        client.post(f"{base}/segments/1/test-shot/approve")
        r = client.post(f"{base}/segments/1/batch/next")
        check("🔴 生成照樣成功（統計寫不進去不擋使用者）", r.status_code == 200, detail_of(r))
        _, seg = reload_segment()
        if seg.clips:
            r = client.post(f"{base}/segments/1/batch/{seg.clips[0].shot_id}/approve")
            check("🔴 通過照樣成功", r.status_code == 200, detail_of(r))
    finally:
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        if ledger._local_mode():  # noqa: SLF001
            local_db.LocalClient.__init__ = _real_init
        ledger.reset_connection()
        check(
            "🔴 還原連線設定（不還原的話清理會靜默失敗）",
            ledger.is_available(),
        )

    # ── 11. Rewind preview 不改狀態 ────────────────────────────────
    print()
    print("[11] rewind preview 只回失效清單，不動任何 state")
    state_before, seg_before = reload_segment()
    stage_before = state_before.stage
    clips_before = len(seg_before.clips)
    r = client.get(f"{base}/rewind/preview?target=look_chosen")
    check("preview API 回 200", r.status_code == 200, detail_of(r))
    if r.status_code == 200:
        body = r.json()
        check("preview 帶 invalidated 清單", "invalidated" in body)
        check(
            "  target_label 給人看（不是 enum 值）",
            bool(body.get("target_label")),
            body.get("target_label", ""),
        )
    state_after_preview, seg_after_preview = reload_segment()
    check(
        "🔴 preview 沒改 stage",
        state_after_preview.stage == stage_before,
        f"before={stage_before.value} after={state_after_preview.stage.value}",
    )
    check(
        "🔴 preview 沒清 clips",
        len(seg_after_preview.clips) == clips_before,
    )
    r = client.get(f"{base}/rewind/preview?target=bogus_stage")
    check(
        "無效階段回 400（不是 500）",
        r.status_code == 400,
        detail_of(r),
    )

    # ── 12. Rewind commit —— 清 state 但不動媒體檔 ────────────────
    print()
    print("[12] rewind commit 清失效資料，但媒體檔一律留給媒體庫")
    fake_png = b"\x89PNG\r\n\x1a\n" + b"0" * 100
    fake_uri = media_store.save(
        TEST_PROJECT, "rewind_fixture", fake_png, "image/png"
    )
    media_path = media_store.path_for(fake_uri)
    check(
        "先寫一張假圖進媒體庫當 fixture",
        media_path is not None and media_path.exists(),
    )

    # target 選 shots_planned（保證比任何生成階段更早）——
    #    測「rewind 真的清 clips」要退到 GENERATING 之前，
    #    退到跟當前同一階段等於 no-op（rewind_to 直接回 []）。
    r = client.post(f"{base}/rewind", json={"target": "shots_planned"})
    check("rewind commit API 回 200", r.status_code == 200, detail_of(r))
    if r.status_code == 200:
        body = r.json()
        cleared = body.get("cleared", {})
        check("回應帶 cleared 統計", isinstance(cleared, dict))
        check(
            "  新 stage 是 shots_planned（或更早）",
            body.get("new_stage") in {"shots_planned", "parsed", "script_uploaded", "created"},
            body.get("new_stage", ""),
        )
    state_after, seg_after = reload_segment()
    check(
        "🔴 stage 真的退回了",
        state_after.stage.order <= Stage.SHOTS_PLANNED.order,
        state_after.stage.value,
    )
    check("clips 清空", len(seg_after.clips) == 0, f"還剩 {len(seg_after.clips)} 個")
    check("output_uri 清掉", not seg_after.output_uri)
    check(
        "🔴 rewind 不刪媒體檔（媒體庫管、不是狀態機管）",
        media_path is not None and media_path.exists(),
    )
    check(
        "🔴 rewind 不動 ledger（學到的東西不隨 rewind 消失）",
        ledger_count() > 0,
        f"還有 {ledger_count()} 筆",
    )

    # ── 13. Media list —— 看得到剛才寫的檔 ────────────────────────
    print()
    print("[13] 媒體庫列表看得到剛才寫的 fixture")
    r = client.get(f"{base}/media")
    check("media list API 回 200", r.status_code == 200, detail_of(r))
    if r.status_code == 200:
        body = r.json()
        check("count > 0", body.get("count", 0) > 0, f"{body.get('count')} 個")
        items = body.get("items") or []
        found = any(
            (i.get("filename") or "").startswith("rewind_fixture") for i in items
        )
        check("清單裡有 rewind_fixture.png", found)
        if items:
            first = items[0]
            check("  每項有 kind 欄位", "kind" in first, first.get("kind", ""))
            check("  每項有 size_bytes", first.get("size_bytes", 0) > 0)
            check("  每項有 uri", bool(first.get("uri")))

    # ── 14. Media delete —— 單獨刪一個 ────────────────────────────
    print()
    print("[14] 媒體庫單獨刪除 + 冪等")
    r = client.delete(f"{base}/media/rewind_fixture.png")
    check("delete API 回 200", r.status_code == 200, detail_of(r))
    if r.status_code == 200:
        check("回應說 deleted=True", r.json().get("deleted") is True)
    check(
        "🔴 檔案真的消失了",
        media_path is not None and not media_path.exists(),
    )
    r = client.delete(f"{base}/media/rewind_fixture.png")
    check(
        "🔴 重複刪回 200 不噴 500（冪等）",
        r.status_code == 200,
        detail_of(r),
    )
    # path traversal：用不含 slash 但含 `..` 的檔名。
    # `..%2Fetc%2Fpasswd` decode 後有 slash、被 FastAPI 判成 route 不存在（404）
    # 檢查根本沒觸發到；改用 `..hidden` 這種 route 會 match 到但被
    # _safe_filename 擋下的 filename。
    r = client.delete(f"{base}/media/..hidden")
    check(
        "🔴 path traversal 擋下來（含 `..` 的檔名）",
        r.status_code == 400,
        detail_of(r),
    )

    # ── 15. 一鍵模式 ───────────────────────────────────────────────
    #
    # [12] 的 rewind 把 stage 退到 shots_planned、試拍也清掉了，
    #    所以這裡要先把試拍閘門重新過一次才能測批次。
    print()
    print("[15] 一鍵模式 —— 放行「等確認」閘門，但不自動通過")
    # 第 12 組的 rewind 把選角解鎖了（那是**正確行為** —— 退回選角之前
    #    本來就該解鎖）。但後面每一項生成都會被第 5 關的閘門擋住，
    #    所以測試得自己把選角補回來，就像 build_fixture 那樣。
    state_r, _ = reload_segment()
    if state_r.casting:
        for session in state_r.casting.sessions:
            if session.selected_candidate_id is None:
                existing = session.all_candidates()
                session.selected_candidate_id = (
                    existing[0].candidate_id
                    if existing
                    else f"_fixture_{session.character_name}"
                )
        project_store.get_store().save(state_r)

    client.post(f"{base}/segments/1/test-shot")
    client.post(f"{base}/segments/1/test-shot/approve")

    r = client.post(f"{base}/segments/1/batch/next")
    check("先生一鏡（不確認）", r.status_code == 200, detail_of(r))

    r = client.post(f"{base}/segments/1/batch/next")
    check(
        "🔴 逐鏡模式仍然擋住（預設沒變）",
        r.status_code == 409,
        detail_of(r),
    )

    # 比對「auto 前後」的數字，不是「全部 clips 的狀態」——
    #    前面第 10 組降級測試留了一鏡 APPROVED 在這一段裡，
    #    斷言「沒有任何一鏡是 approved」會抓到那一鏡而誤報。
    #    要問的是「auto 這一次有沒有自動通過」，不是「這段有沒有通過的鏡頭」。
    _, seg_before = reload_segment()
    approved_before = sum(
        1 for c in seg_before.clips if c.status is ClipStatus.APPROVED
    )
    generated_before = sum(1 for c in seg_before.clips if c.uri)

    r = client.post(f"{base}/segments/1/batch/next?auto=true")
    check("🔴 一鍵模式放行", r.status_code == 200, detail_of(r))

    _, seg = reload_segment()
    generated_after = sum(1 for c in seg.clips if c.uri)
    approved_after = sum(1 for c in seg.clips if c.status is ClipStatus.APPROVED)
    check(
        "  真的多生了一鏡",
        generated_after > generated_before,
        f"{generated_before} → {generated_after} 鏡",
    )
    awaiting = [c for c in seg.clips if c.status is ClipStatus.AWAITING_APPROVAL]
    check(
        "🔴 一鍵生出來的仍然是「待確認」（一鍵 ≠ 自動通過）",
        len(awaiting) >= 2,
        f"{len(awaiting)} 鏡待確認",
    )
    check(
        "🔴 auto 沒有自動 approve 任何一鏡",
        approved_after == approved_before,
        f"approved {approved_before} → {approved_after}（沒變才對）",
    )

    # auto 只放行「等確認」，不放行試拍閘門
    state_tmp, seg_tmp = reload_segment()
    saved_approved = seg_tmp.test_shot_approved
    seg_tmp.test_shot_approved = False
    project_store.get_store().save(state_tmp)
    r = client.post(f"{base}/segments/1/batch/next?auto=true")
    check(
        "🔴 auto 不能跳過試拍閘門（那是第 7 關的錢閘）",
        r.status_code == 409,
        detail_of(r),
    )
    state_tmp, seg_tmp = reload_segment()
    seg_tmp.test_shot_approved = saved_approved
    project_store.get_store().save(state_tmp)

    # ── 16. 燒字幕進影片 ───────────────────────────────────────────
    print()
    print("[16] 字幕燒進影片 —— 接上零呼叫者的 burn_subtitles")
    state_tmp, seg_tmp = reload_segment()
    seg_tmp.output_uri = None
    project_store.get_store().save(state_tmp)
    r = client.post(f"{base}/segments/1/assemble/burn?lang=zh")
    check(
        "🔴 還沒接片就燒 → 409（不是 500）",
        r.status_code == 409,
        detail_of(r),
    )

    # 造一支「成片」放進媒體庫，讓 route 讀得到。
    # burn_subtitles 換成樁 —— 真的跑 ffmpeg 重新編碼要好幾分鐘。
    fake_mp4 = b"\x00\x00\x00\x18ftypmp42" + b"0" * 200
    out_uri = media_store.save(TEST_PROJECT, "segment1_final", fake_mp4, "video/mp4")
    state_tmp, seg_tmp = reload_segment()
    seg_tmp.output_uri = out_uri
    seg_tmp.output_seconds = 22.0
    project_store.get_store().save(state_tmp)

    burn_calls: list[int] = []

    def _stub_burn(video, srt, **kw):
        burn_calls.append(len(video))
        assert srt.strip(), "燒字幕不該拿到空的 SRT"
        # 回傳形狀跟真的一樣：(影片位元組, 字型是否缺失)。
        # 樁的形狀跟本尊分岔的話，測試會對著一個不存在的介面通過。
        return video + b"BURNED", False

    original_burn = assembler.burn_subtitles
    assembler.burn_subtitles = _stub_burn
    try:
        r = client.post(f"{base}/segments/1/assemble/burn?lang=zh")
        check("燒字幕 API 回 200", r.status_code == 200, detail_of(r))
        if r.status_code == 200:
            body = r.json()
            check("🔴 burn_subtitles 真的被呼叫", len(burn_calls) == 1, f"{len(burn_calls)} 次")
            check("  回傳下載連結", bool(body.get("download_url")), body.get("download_url", ""))
            check(
                "  檔名帶語言、不會蓋掉原成片",
                body.get("filename") == "segment1_zh_burned.mp4",
                body.get("filename", ""),
            )
            burned_path = media_store.path_for(body.get("uri", ""))
            check(
                "🔴 燒好的檔案真的存進媒體庫",
                burned_path is not None and burned_path.exists(),
            )
            check(
                "🔴 原始成片沒被覆蓋（想換語言還回得去）",
                media_store.load(out_uri) is not None,
            )
        # 燒好的檔案要出現在媒體庫清單裡（配合死檔案策略：使用者自己挑要留哪個）
        r = client.get(f"{base}/media")
        names = [i["filename"] for i in r.json().get("items", [])]
        check(
            "🔴 燒好的影片出現在媒體庫（可以自己刪）",
            "segment1_zh_burned.mp4" in names,
            f"{len(names)} 個檔案",
        )
    finally:
        assembler.burn_subtitles = original_burn

    # ── 16b. 給使用者的文案不含工程日誌 ────────────────────────────
    print()
    print("[16b] constraints 的 principle 只寫給使用者")
    from app.services import config_loader

    leaks = config_loader._verify_principle_audience()  # noqa: SLF001
    check(
        "🔴 沒有工程日誌洩漏到使用者文案",
        not leaks,
        leaks[0][:90] if leaks else "18 條都乾淨",
    )
    # 順便驗「檢查本身是活的」——「通過」與「檢查有效」是兩件事，
    #    今天已經被這個騙過一次（record_generation 零呼叫者）。
    saved_constraints = config_loader.constraints
    config_loader.constraints = lambda: {
        "rules": [{"id": "_probe", "principle": "2026-08-08 改了 s001_06 的寫法。"}]
    }
    try:
        check(
            "  檢查本身抓得到違規（不是死的）",
            len(config_loader._verify_principle_audience()) == 1,  # noqa: SLF001
        )
    finally:
        config_loader.constraints = saved_constraints

    advisories_with_text = [
        r
        for r in config_loader.constraints().get("rules", [])
        if str(r.get("advisory", "")).strip()
    ]
    check(
        "🔴 提醒走明確欄位、不靠 emoji 猜",
        len(advisories_with_text) >= 1,
        f"{len(advisories_with_text)} 條有 advisory："
        + "、".join(r["id"] for r in advisories_with_text),
    )

    # ── 17. 全片合成 ───────────────────────────────────────────────
    #
    # 測試素材只留了一段（build_fixture 砍成 segments[:1]），
    # 所以這裡驗的是「一段也能成片」+「缺段誠實回報」+「字幕時間軸」。
    print()
    print("[17] 全片合成 —— 段落接成一部片")
    state_tmp, seg_tmp = reload_segment()
    # 上一組把 output_uri 指到假 mp4，正好當全片的來源
    check("段落有成片可以接", bool(seg_tmp.output_uri), seg_tmp.output_uri or "（無）")

    r = client.post(f"{base}/assemble-film")
    check("全片合成 API 回 200", r.status_code == 200, detail_of(r))
    if r.status_code == 200:
        body = r.json()
        check("  回傳全片下載連結", bool(body.get("download_url")))
        check(
            "  段落數對得上",
            body.get("segment_count") == 1,
            str(body.get("segment_count")),
        )
        check(
            "🔴 誠實回報缺了哪幾段",
            "missing_segments" in body,
            f"缺 {body.get('missing_segments')}",
        )

    state_after_film, _ = reload_segment()
    check(
        "🔴 全片 URI 存進專案狀態",
        bool(state_after_film.film_output_uri),
        state_after_film.film_output_uri or "（無）",
    )
    check(
        "🔴 段落成片沒被全片取代（重接某一段還回得去）",
        bool(reload_segment()[1].output_uri),
    )

    r = client.get(f"{base}/film/download")
    check("全片下載回 200", r.status_code == 200, f"{r.status_code} {len(r.content)} bytes")

    # 全片字幕：時間軸要帶偏移，不是各段 srt 直接相接
    r = client.get(f"{base}/film/subtitles?lang=zh")
    check("全片字幕 API 回 200", r.status_code == 200, detail_of(r))
    if r.status_code == 200:
        srt = r.text
        check("  是 SRT 格式", "-->" in srt, srt.splitlines()[0][:20] if srt else "")
        check("  字幕從 1 號開始重新編號", srt.strip().startswith("1"))

    # 全片燒字幕（樁掉真正的 ffmpeg）
    burn_calls.clear()
    assembler.burn_subtitles = _stub_burn
    try:
        r = client.post(f"{base}/film/burn?lang=zh")
        check("全片燒字幕回 200", r.status_code == 200, detail_of(r))
        if r.status_code == 200:
            body = r.json()
            check("🔴 burn_subtitles 真的被呼叫", len(burn_calls) == 1)
            check(
                "  檔名是 film_zh_burned.mp4",
                body.get("filename") == "film_zh_burned.mp4",
                body.get("filename", ""),
            )
        state_x, _ = reload_segment()
        check(
            "🔴 原始全片沒被覆蓋",
            media_store.load(state_x.film_output_uri or "") is not None,
        )
    finally:
        assembler.burn_subtitles = original_burn

    # ── 18. 學習迴路：首頁與專案內頁必須同形狀 ──────────────────────
    print()
    print("[18] 學習迴路 API（首頁 = 全站範圍、專案內頁 = 單專案，形狀必須一致）")

    # 為什麼要測形狀而不只是測 200：
    #    首頁那支把 distinct_shots 讀成 stats["shots"]（正確 key 是 distinct_shots），
    #    「涵蓋鏡頭」靜默顯示 0 顯示了三天 —— 專案內頁同一格卻是對的，
    #    因為它們曾經是兩份 code。**兩份實作必然分岔**，前端也因此曾是兩個組件。
    #    現在前端共用同一個 LearningPanel，所以「同形狀」從慣例變成硬需求：
    #    這一項就是把它釘住的地方。
    r_proj = client.get(f"{base}/learning")
    r_home = client.get("/api/config/learning-overview")
    check("專案內頁 /learning 回 200", r_proj.status_code == 200, detail_of(r_proj))
    check("首頁 /config/learning-overview 回 200", r_home.status_code == 200, detail_of(r_home))

    if r_proj.status_code == 200 and r_home.status_code == 200:
        proj_body, home_body = r_proj.json(), r_home.json()
        missing = sorted(set(proj_body) - set(home_body))
        check(
            "🔴 首頁欄位不缺（前端共用同一個組件，缺一欄就少一塊 UI）",
            not missing,
            f"缺少：{missing}" if missing else "欄位齊全",
        )
        # 這幾欄是面板實際會讀的，型別錯了畫面就會顯示 undefined
        for key in ("database", "connected", "records", "distinct_shots", "rejected", "refine_threshold"):
            check(
                f"  {key} 型別與專案內頁一致",
                type(home_body.get(key)) is type(proj_body.get(key)),
                f"{type(home_body.get(key)).__name__} vs {type(proj_body.get(key)).__name__}",
            )
        # 全站範圍 ⊇ 單專案範圍 —— 取錯 key 的那個 bug 正好會在這裡露出來
        check(
            "🔴 全站紀錄數 ≥ 單專案（取錯 key 會讓它變 0）",
            home_body.get("records", 0) >= proj_body.get("records", 0),
            f"全站 {home_body.get('records')} · 本專案 {proj_body.get('records')}",
        )
        check(
            "🔴 全站涵蓋鏡頭數 ≥ 單專案",
            home_body.get("distinct_shots", 0) >= proj_body.get("distinct_shots", 0),
            f"全站 {home_body.get('distinct_shots')} · 本專案 {proj_body.get('distinct_shots')}",
        )
        check(
            "  提煉門檻讀的是 refinery 常數，不是寫死的數字",
            home_body.get("refine_threshold") == refinery.MIN_SAMPLES_TO_REFINE,
            str(home_body.get("refine_threshold")),
        )

    r_rows_home = client.get("/api/config/learning-records?limit=5")
    r_rows_proj = client.get(f"{base}/learning/records?limit=5")
    check("首頁原始紀錄回 200", r_rows_home.status_code == 200, detail_of(r_rows_home))
    check("專案原始紀錄回 200", r_rows_proj.status_code == 200, detail_of(r_rows_proj))
    if r_rows_home.status_code == 200 and r_rows_proj.status_code == 200:
        home_rows, proj_rows = r_rows_home.json(), r_rows_proj.json()
        check(
            "🔴 兩邊欄位一致（同一個表格組件在渲染）",
            home_rows.get("columns") == proj_rows.get("columns"),
            f"{home_rows.get('columns')}",
        )
        check(
            "🔴 首頁真的拿得到原始列（舉證責任：統計可以編，原始列編不出來）",
            bool(home_rows.get("rows")),
            f"{len(home_rows.get('rows') or [])} 列",
        )

    # ── 19. 複製與刪除 —— 整條鏈都要對 ─────────────────────────────
    print()
    print("[19] 複製專案 / 刪除專案（整條鏈，不能刪一半留一半）")

    r = client.post(f"{base}/duplicate")
    check("複製 API 回 200", r.status_code == 200, detail_of(r))
    copy_id = ""
    if r.status_code == 200:
        body = r.json()
        copy_id = body.get("project_id", "")
        check("  拿到新專案 ID", bool(copy_id), copy_id)
        copied = project_store.get_store().load(copy_id)
        check("🔴 劇本沿用（不用再付一次解析費）", copied is not None and copied.screenplay is not None)
        check(
            "🔴 進度歸零",
            copied is not None and not copied.segments and copied.spend.total_usd == 0,
            f"segments={len(copied.segments) if copied else '?'} spent=${copied.spend.total_usd if copied else '?'}",
        )
        check(
            "🔴 原專案完全不受影響",
            project_store.get_store().load(TEST_PROJECT) is not None,
        )

    # 刪除：先預覽、再刪、然後確認媒體真的沒了
    probe_uri = media_store.save(TEST_PROJECT, "delete_probe", b"X" * 512, "image/png")
    probe_path = media_store.path_for(probe_uri)
    check("先放一個檔案當刪除的靶", probe_path is not None and probe_path.exists())

    r = client.get(f"{base}/delete/preview")
    check("刪除預覽回 200", r.status_code == 200, detail_of(r))
    if r.status_code == 200:
        pv = r.json()
        check(
            "  講明會刪幾個檔、多少空間",
            pv["will_delete"]["media_files"] > 0 and pv["will_delete"]["media_bytes"] > 0,
            f"{pv['will_delete']['media_files']} 檔 · {pv['will_delete']['media_bytes']} bytes",
        )
        check(
            "🔴 也講明**不會**刪什麼（誠實原則）",
            bool(pv.get("will_keep", {}).get("learning_records")),
        )
    ledger_before_delete = ledger_count()

    r = client.delete(f"{base}")
    check("刪除 API 回 200", r.status_code == 200, detail_of(r))
    if r.status_code == 200:
        body = r.json()
        check("  回報刪了幾個檔", body.get("deleted_files", 0) > 0, str(body.get("deleted_files")))
    check(
        "🔴 媒體檔真的沒了（不是只刪狀態）",
        probe_path is not None and not probe_path.exists(),
    )
    check("🔴 專案狀態也沒了", project_store.get_store().load(TEST_PROJECT) is None)
    check(
        "ledger 保留（記憶與架構不能刪）",
        ledger_count() == ledger_before_delete,
        f"{ledger_before_delete} → {ledger_count()} 筆",
    )

    # 複製出來的那份也清掉，不要留垃圾
    if copy_id:
        client.delete(f"/api/projects/{copy_id}")

    # ── 收尾 ───────────────────────────────────────────────────────
    print()
    print("=" * 72)
    print(f"通過 {len(_passed)} 項，失敗 {len(_failed)} 項")
    if _failed:
        print()
        for name in _failed:
            print(f"  X {name}")
    print()
    print(f"Veo 被呼叫 {len(_gen_calls)} 次 —— **全部是樁，實際花費 $0**")
    print("=" * 72)
    return 1 if _failed else 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        drop_test_db()
        shutil.rmtree(_TMP_STORE, ignore_errors=True)
    raise SystemExit(code)
