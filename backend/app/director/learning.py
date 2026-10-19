"""學習記憶庫：每導完一場就記一筆，下一場開始前，把「你最常犯的錯」交給導演。

為什麼要有：五次雲端紀錄裡，每一次第一回合都犯一樣的錯（秒數不夠、兩人同框沒點名、寫到嘴或呼吸）。
導演沒有記憶，每一場都從頭犯一遍，光改這些就吃掉好幾回合、二十幾萬 token。
規則寫在文件裡它讀過了 —— 讀過不等於會避開。把「你自己過去最常在哪裡被擋」講給它聽，才是它的經驗。

記什麼（一場一筆）：每一回合被哪幾條規則擋、幾回合過關、評審說了什麼、用了多少 token、最後結果。
放哪裡：有 RENDER_BUCKET 就放 GCS（Cloud Run 重開不會掉），沒有就放本機 outputs/learning/。
怎麼用：
  mistakes_brief() → 導演開工前看的「最常犯的錯」（只看第一回合：那才是它不經提醒時的樣子）
  progress()       → 一場一場的數字（第一回合錯幾個、幾回合過關、多少 token），證明有沒有越做越好
"""

import json
import os
import pathlib
import time
from collections import Counter

ROOT = pathlib.Path(__file__).parent
def _local():
    """導演成績單的資料夾：使用者資料家目錄底下（Greenlight Local；每次現查，測試換家目錄也跟得上）。"""
    from ..services.env_boot import data_home
    return data_home() / "director_learning"

# 每條規則「怎麼避開」的一句話 —— 放在提醒裡，比只說「你常錯規則 8」有用
HOW_TO_AVOID = {
    "rule 3": "never write mouth, lips, smiling, breathing, sighing or speaking in the performance line",
    "rule 4": "put a visible body action (hand, step, turn) before the last beat of every speaking shot",
    "rule 7": "no shot over 10 s — split long lines at a sentence end",
    "rule 8": "use the minimum-seconds table: each shot must be at least as long as its lines need",
    "rule 9": "copy every script line once, in order; split only at sentence ends; never repeat a line",
    "rule 15": "pick a spot from render_zh.spots that matches where the characters are",
    "rule 16": "a speaking shot never says the character stays silent",
    "rule 17": "no phone or typing while speaking",
    "rule 21": "when two people are in frame, name both (月音 / Lisa) in the performance line",
    "rule 22": "vary framing and let the camera move with the emotion",
    "rule 23": "never reuse the same action phrase in 3+ shots — reactions escalate",
    "rule 24": "every split line ends at a sentence end (。？！——)",
    "rule 25": "no miming and no 代表/表示/象徵 — real body actions only",
    "rule 28": "no 眼裡/心裡/淚珠, no line of 3 characters or fewer on its own (merge it with the line before or after)",
    "rule 29": "an action that changes something has a start and a result (切成兩半, not 切著)",
    "rule 30": "two people with no first frame: the first clause says where each one stands",
    "rule 31": "no DOLLY_OUT",
    "enums": "use only the listed shot_type / camera_move / voice_role values",
    "format": "return only the JSON object, keep the reasoning short",
}


class Store:
    """一場一個 JSON：runs/<時間>-<場次>.json。GCS 或本機二選一，介面一樣。"""

    def __init__(self):
        # Greenlight Local：導演的成績單放使用者自己的資料家目錄（跟專案、學習紀錄同一個家），不放雲端
        self.bucket = None
        (_local() / "runs").mkdir(parents=True, exist_ok=True)

    def put(self, name: str, data: dict):
        text = json.dumps(data, ensure_ascii=False)
        if self.bucket:
            self.bucket.blob(f"learning/{name}").upload_from_string(text, content_type="application/json")
        else:
            (_local() / name).parent.mkdir(parents=True, exist_ok=True)
            (_local() / name).write_text(text, encoding="utf-8")

    def runs(self) -> list:
        if self.bucket:
            items = [json.loads(b.download_as_text()) for b in self.bucket.list_blobs(prefix="learning/runs/")]
        else:
            items = [json.loads(f.read_text(encoding="utf-8")) for f in sorted((_local() / "runs").glob("*.json"))]
        return sorted(items, key=lambda r: r.get("at", 0))


def summarize(scene_id: str, done: dict, source: str = "live", at: float = None) -> dict:
    """把一場導演紀錄（done 事件，含 trace）濃縮成一筆學習紀錄。"""
    rounds = [t for t in done.get("trace") or [] if t.get("type") != "critique"]
    critiques = [t for t in done.get("trace") or [] if t.get("type") == "critique"]

    def rule_counts(t):
        return dict(Counter(i["rule"] for i in t.get("issues") or [] if i.get("level") == "ERROR"))

    # 「第一份草稿」＝第一個真的交出分鏡的回合。回覆是空的、解析不了的回合不算草稿 ——
    # 實測：第一回合思考完回了空的，被記成「第一回合只錯 1 個」，成績單好看得假（量表自己會騙人）。
    drafts = [t for t in rounds if (t.get("shots") or 0) > 0]
    first = drafts[0] if drafts else None
    first_pass = next((t["round"] for t in rounds if t.get("errors") == 0), None)
    return {
        "scene_id": scene_id, "source": source, "at": at or time.time(),
        "status": done.get("status"),
        "rounds": len(rounds),
        "empty_or_unparseable_rounds": len(rounds) - len(drafts),
        "first_pass_round": first_pass,
        "round1_errors": first.get("errors") if first else None,
        "round1_rules": rule_counts(first) if first else {},
        "rules_by_round": [rule_counts(t) for t in rounds],
        "tokens": sum((t.get("usage") or {}).get("total_tokens", 0) for t in done.get("trace") or []),
        "critiques": [{"verdict": c.get("verdict"), "notes": [n.get("problem") for n in c.get("notes") or []]} for c in critiques],
        "shots": len(done.get("shots") or []),
        "hints_given": done.get("hints_given", False),
    }


def record(scene_id: str, done: dict, source: str = "live", at: float = None, store: Store = None) -> dict:
    store = store or Store()
    s = summarize(scene_id, done, source, at)
    store.put(f"runs/{int(s['at'])}-{scene_id}.json", s)
    # 第三步：評審點頭的版本收進範例庫。只收「關卡全過＋評審核准」的 —— 只過關卡的不收（平淡也會過關卡）。
    last_review = next((t for t in reversed(done.get("trace") or []) if t.get("type") == "critique"), None)
    if done.get("status") == "passed" and last_review and last_review.get("verdict") == "approve":
        store.put(f"approved/{scene_id}-{int(s['at'])}.json",
                  {"scene_id": scene_id, "at": s["at"], "shots": done.get("shots"), "review": last_review.get("overall")})
    return s


def approved_examples(exclude_scene: str, store: Store = None, limit: int = 1) -> list:
    """評審核准過的分鏡，給導演當範例 —— 🔴 永遠不給同一場（那是抄自己的答案，不是學手藝）。"""
    store = store or Store()
    if store.bucket:
        items = [json.loads(b.download_as_text()) for b in store.bucket.list_blobs(prefix="learning/approved/")]
    else:
        items = [json.loads(f.read_text(encoding="utf-8")) for f in (_local() / "approved").glob("*.json")] if (_local() / "approved").exists() else []
    items = [x for x in items if x.get("scene_id") != exclude_scene]
    return sorted(items, key=lambda x: x.get("at", 0), reverse=True)[:limit]


def mistakes_brief(store: Store = None, top: int = 6) -> str:
    """「你最常犯的錯」：看過去每一場的第一回合，算每條規則出現在幾成的場次裡。

    只看第一回合 —— 後面的回合已經被關卡提醒過了，那不是它自己的習慣。
    沒有紀錄就回空字串（第一次跑沒有經驗可講，不要編）。
    """
    runs = [r for r in (store or Store()).runs() if r.get("round1_rules") is not None]
    if not runs:
        return ""
    seen = Counter()
    for r in runs:
        for rule in r["round1_rules"]:
            seen[rule] += 1
    lines = [f"- {rule}: in {n} of {len(runs)} past first drafts → {HOW_TO_AVOID.get(rule, 'see the rules')}"
             for rule, n in seen.most_common(top)]
    return ("## Your own track record\n"
            f"Across your last {len(runs)} scenes, these are the rules your FIRST draft broke most often. "
            "Check each one before you answer — every miss costs a full extra round.\n" + "\n".join(lines))


def progress(store: Store = None) -> list:
    """一場一場的成績，給網頁畫「有沒有越做越好」。"""
    keep = ("scene_id", "source", "at", "status", "rounds", "first_pass_round", "round1_errors", "tokens", "shots", "hints_given")
    return [{k: r.get(k) for k in keep} for r in (store or Store()).runs()]
