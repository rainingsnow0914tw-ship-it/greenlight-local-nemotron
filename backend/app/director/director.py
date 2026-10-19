"""導演代理：Nemotron 把一場戲切成分鏡、寫每一鏡的表演句，被關卡擋下就自己改。

迴圈：寫稿 → gates.check() → 有 ERROR 就把「哪一鏡、違反第幾條、為什麼」原封不動交回去 → 改稿
      → 直到零 ERROR 或用完回合數。每一回合都記進 trace，示範時看得到它怎麼被擋、怎麼改。

為什麼關卡回饋要附規則編號與原因：只說「錯了」，模型會亂改別的地方；
說「s029_10 違反規則 7：11 秒超過 10 秒，要拆成兩鏡、不能刪字」，它才改得對地方。

用法：python director.py scenes/s029_first_words.json            （要 NEBIUS_API_KEY、DIRECTOR_MODEL）
      python director.py scenes/s029_first_words.json --offline  （不打 API，用參考分鏡測迴圈本身）
"""

import json
import logging
import os
import pathlib
import re
import sys
import time

import httpx

from . import gates

logger = logging.getLogger(__name__)

ROOT = pathlib.Path(__file__).parent
TOKEN_FACTORY_BASE = os.environ.get("TOKEN_FACTORY_BASE", "https://api.tokenfactory.nebius.com/v1")
MAX_ROUNDS = int(os.environ.get("DIRECTOR_MAX_ROUNDS", "10"))   # 穩定測試第三輪：沒過的那場 32→6→5→4→3→1 一路在收斂、只是回合用完
# 每次呼叫最多想多少 tokens（見 call_with_tools 的說明）
REASONING_BUDGET = int(os.environ.get("REASONING_BUDGET", "12000"))
# 評審最多看幾次（每次看完要改、改完再過關卡）；0＝不請評審
CRITIC_ROUNDS = int(os.environ.get("CRITIC_ROUNDS", "2"))
# 每次評審之後另外給幾回合改（改完要重新過關卡）
REVISION_ROUNDS = int(os.environ.get("REVISION_ROUNDS", "4"))
# 錯誤連續幾回合沒創新低就停、交錯最少那版（第三世：每段錯誤卡在 2～4 個還跑滿 10 回合，一段 12 分鐘白跑一半）。
#    只要還在創新低就繼續跑（那場 32→6→5→4→3→1 是一路收斂、不能停）。剩下的錯誤由生成前關卡擋下來給人看。
PATIENCE = int(os.environ.get("DIRECTOR_PATIENCE", "3"))

SHOT_SCHEMA = """Return ONLY a JSON object, no prose, in exactly this shape:
{"shots": [
  {"shot_id": "s029_01", "duration_seconds": 8, "shot_type": "MEDIUM", "camera_move": "BREATHE",
   "characters_in_frame": ["luna"], "spot": "<a key of render_zh.spots>", "speaker": "luna", "voice_role": "character",
   "blocking": "<side_by_side|face_to_face, only when two people are in frame>",
   "dialogue_lines": [{"speaker": "lisa", "text": "..."}, {"speaker": "luna", "text": "..."}],  // only when two people speak in one shot; then dialogue = these joined
   "dialogue": "<script line or empty string>",
   "visual_prompt": "<performance line in Traditional Chinese: 3-5 body actions joined by 之後/然後/最後>",
   "visual_prompt_en": "<faithful English translation of visual_prompt, for human reviewers>",
   "reasoning_en": "<one short sentence: why this shot>"}
]}
shot_id is the scene id plus a two-digit running number. speaker is null when there is no dialogue. Omit dialogue_lines and blocking when they do not apply. No comments in the JSON."""


def system_prompt(scene_id: str = "") -> str:
    """導演的工作說明＝規則全文＋輸出格式。規則只有一個家（rules/directing_rules.md），這裡只讀不抄。"""
    from ..services import knowledge   # 使用者知識庫那一份（安裝檔外面、隨時可改）
    rules = knowledge.read_text("directing_rules.md")
    return (
        "You are the film director for an anime series. You turn one script scene into a shot list "
        "for a video model that renders picture and voice together. Follow the directing rules exactly.\n\n"
        f"{rules}\n\n{house_style(scene_id)}\n\n{SHOT_SCHEMA}"
        + ("\n\n## Your tools\nYou can call tools while you work. Use speech_seconds whenever you set a duration "
           "instead of guessing. Before your final answer, call check_shots on your full draft and fix every ERROR it "
           "reports (check again after big changes). Use lookup_rule when unsure what a rule demands. "
           "Your final answer is the JSON object only." if USE_TOOLS else "")
    )


def house_style(scene_id: str = "") -> str:
    """人工團隊導過、已經發布的兩場戲，當成範例給模型看「我們家怎麼導」。

    為什麼要範例不要更多規則：實測，規則和關卡只擋得住「會壞」與「量得出來的平」，
    Nemotron 導第 61 場 20 鏡只有 2 鏡在動，人工版 14 鏡有 11 鏡在動 —— 鏡頭怎麼跟著情緒走，
    寫成規則寫不出來，看一次範例就懂。
    🔴 範例刻意不放第 61 場本身（那是考題的答案，放了就是抄）；放同一章、同類型的別場。
    """
    f = ROOT / "examples" / "director_examples.json"
    if not f.exists():
        return ""
    ex = json.loads(f.read_text(encoding="utf-8"))["examples"]
    out = ("## House style: two scenes the human team directed for this series\n"
           "Study how the camera moves with the emotion, how the listener reacts between lines, how long lines "
           "are split, and how few shots a scene needs. Do not copy their content — direct your own scene the same way.\n"
           + json.dumps(ex, ensure_ascii=False, indent=1))
    # 第三步：自己導過、評審核准的別場戲也拿來當範例（learning.approved_examples 保證不會是同一場）
    try:
        from . import learning
        mine = learning.approved_examples(scene_id)
    except Exception:
        mine = []
    if mine:
        out += ("\n\n## One of your own scenes the producer approved\n"
                + json.dumps([{"scene": m["scene_id"], "producer": m.get("review"), "shots": m["shots"]} for m in mine],
                             ensure_ascii=False, indent=1))
    return out


def parse_json(text: str) -> dict:
    """從模型回覆裡挖出 JSON 物件。

    推理模型可能先吐 <think>…</think> 或包一層 ```json，所以：先去掉思考段，
    再抓第一個 { 到最後一個 } 之間解析。解析失敗就丟例外，讓迴圈把錯誤當回饋送回去。
    """
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b < 0:
        raise ValueError("no JSON object in the reply")
    return json.loads(text[a:b + 1])


PATCH_ASK = (' Reply with only the shots you change, as {"changes": [<complete shot objects — reuse a shot_id to replace '
             'that shot, use a new id like s061_05b to insert one>], "remove": [<shot_ids to delete>]}. Every shot you do '
             'not list stays exactly as it is.')


def apply_patch(current: list, data: dict) -> list:
    """把改稿（只有改動的鏡）合併回目前這一版：同鏡號換掉、新鏡號插進去、remove 的刪掉，照鏡號排序。

    為什麼改稿只交改動的鏡：雲端實測，每次改稿都整份重寫 20 鏡，修一個壞一個 ——
    第一回合只剩 1 個錯，第二回合重寫完變 30 個。提示詞講了好幾次「只改點名的鏡」都沒用；
    改成架構上就碰不到 —— 沒交的鏡，程式原封不動留著，想改壞也改不到。
    """
    if not isinstance(data.get("changes"), list):
        raise ValueError("revision must have a 'changes' list")
    by_id = {s.get("shot_id"): s for s in current}
    for s in data["changes"]:
        if not isinstance(s, dict) or not s.get("shot_id"):
            raise ValueError("every change needs a shot_id")
        by_id[s["shot_id"]] = s
    for sid in data.get("remove") or []:
        by_id.pop(sid, None)
    return [by_id[k] for k in sorted(by_id)]


def parse_shots(text: str) -> list:
    """從模型回覆裡挖出 shots 陣列（格式不對就丟例外）。"""
    shots = parse_json(text).get("shots")
    if not isinstance(shots, list) or not shots:
        raise ValueError("JSON has no non-empty 'shots' list")
    return shots


def call_token_factory(messages: list, model: str) -> tuple:
    """打 Token Factory 的 chat completions（OpenAI 相容、串流），回傳 (文字, usage)。

    為什麼一定要串流：寫一整場分鏡要先想很久、再吐一大段 JSON，不串流的話連線一分多鐘
    沒有任何資料，會被中間的代理切斷（實測：Server disconnected without sending a response；
    同一時間回答很短的驗收全部正常）。串流讓連線一直有資料在跑。
    斷線時重試一次 —— 只重試連線層的錯，模型回的內容有問題不重試（那要交給迴圈當回饋）。
    """
    text, usage, _ = call_with_tools(messages, model)
    return text, usage


def call_with_tools(messages: list, model: str, tools: list = None, tool_choice: str = None) -> tuple:
    """同上，但可以給工具。回傳 (文字, usage, tool_calls)。

    串流時工具呼叫是一片一片來的（同一個 index 的 name／arguments 分好幾段），要照 index 接回去。
    模型決定用工具時，文字可以是空的 —— 只有「沒文字、也沒工具呼叫」才算思考吃光額度。
    """
    from ..services import settings_store

    # Greenlight Local：金鑰是使用者自己在設定頁填的（加密存在他電腦），不讀環境變數
    key = settings_store.key("nebius") or os.environ.get("NEBIUS_API_KEY")
    if not key:
        raise PermissionError("No Nebius API key — add yours in Settings")
    body = {"model": model, "messages": messages, "temperature": 0.4, "max_tokens": 32768,   # 平台實際上限（見下面 length 重試的說明）
            "stream": True, "stream_options": {"include_usage": True}}
    # 思考設上限：照想，但永遠留一半額度給答案。
    # 量的：交得出分鏡的回合思考中位數 1.4 萬、最多 2.7 萬 tokens；回空的回合思考都超過 3 萬、撞到 32000 上限，
    # 一個字的答案都沒留下（雲端三場 24 回合裡 8 回合是空的）。Token Factory 接受 Nemotron 的 reasoning_budget
    # （實測：上限 2048 → 實際思考 1435；Super／Ultra／Nano 都接受）。設 0 就不限制。
    if REASONING_BUDGET:
        body["chat_template_kwargs"] = {"enable_thinking": True, "reasoning_budget": REASONING_BUDGET}
    if tools:
        body["tools"] = tools
        if tool_choice:
            body["tool_choice"] = tool_choice
    text, usage, finish, tool_calls = _stream(body, key)
    # 思考撞到輸出上限（finish_reason=length）：同一回合馬上不思考再問一次，不浪費一整回合。
    # 雲端三場 42 回合裡 11 回合是空的或 JSON 被切斷，completion 全部停在 32768 ——
    # 平台實際的輸出上限是 32768（max_tokens 給 48000 也一樣），而 reasoning_budget 12000 在長回合裡沒被遵守、一路想到上限。
    # 兩次的用量加總回報，用量數字才是真的。
    if finish == "length" and not tool_calls and REASONING_BUDGET:
        retry = dict(body, chat_template_kwargs={"enable_thinking": False})
        text, usage2, finish, tool_calls = _stream(retry, key)
        usage = {k: (usage.get(k) or 0) + (usage2.get(k) or 0) for k in ("prompt_tokens", "completion_tokens", "total_tokens")}
        usage["retried_without_thinking"] = True
    # Nemotron 先想再答，思考放在 reasoning_content、也吃 max_tokens。
    # 額度被思考吃光時沒有任何回覆內容 —— 明講出來，不要讓下游拿空字串去解析、在別處莫名壞掉。
    # （實測：ping 給 20 tokens，20 個全是思考，回覆是空的。）
    if not text.strip() and not tool_calls:
        raise ValueError(f"empty reply (finish_reason={finish}): the reasoning used up the token budget")
    return text, usage, tool_calls


def _stream(body: dict, key: str) -> tuple:
    """送一次串流請求，回傳 (文字, usage, finish_reason, tool_calls)。斷線重試一次（只重試連線層的錯）。"""
    for attempt in range(2):
        content, usage, finish, calls = [], {}, None, {}
        try:
            with httpx.stream("POST", f"{TOKEN_FACTORY_BASE}/chat/completions",
                              headers={"Authorization": f"Bearer {key}"}, json=body,
                              timeout=httpx.Timeout(60, read=300)) as r:
                if r.status_code != 200:
                    r.read()
                    r.raise_for_status()
                for line in r.iter_lines():
                    if not line.startswith("data: ") or line == "data: [DONE]":
                        continue
                    ev = json.loads(line[6:])
                    usage = ev.get("usage") or usage
                    for ch in ev.get("choices") or []:
                        delta = ch.get("delta") or {}
                        content.append(delta.get("content") or "")
                        for tc in delta.get("tool_calls") or []:
                            c = calls.setdefault(tc.get("index", 0), {"id": "", "type": "function",
                                                                       "function": {"name": "", "arguments": ""}})
                            c["id"] = tc.get("id") or c["id"]
                            fn = tc.get("function") or {}
                            c["function"]["name"] += fn.get("name") or ""
                            c["function"]["arguments"] += fn.get("arguments") or ""
                        finish = ch.get("finish_reason") or finish
            break
        except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ConnectError):
            if attempt:
                raise
    return "".join(content), usage, finish, [calls[i] for i in sorted(calls)]


MAX_TOOL_STEPS = int(os.environ.get("MAX_TOOL_STEPS", "6"))
# 預設不讓模型自己呼叫工具（USE_TOOLS=1 才開）。
# 對照實驗（experiments/ab_tools.py，第 61 場、同一份成績單、各 3 次）：
#   模型自己呼叫工具 → 平均 17 萬 tokens、3 次只過 1 次；不給工具 → 平均 8 萬 tokens、3 次全過。
# 省 token 的是「系統替它用的工具」：先量好的秒數表、成績單、只交改動的鏡、關卡在後面驗 ——
# 讓它自己反覆量、反覆自檢，只是多花錢，所以預設關掉；工具箱留著（tools.py），要比較時再開。
USE_TOOLS = os.environ.get("USE_TOOLS", "0") == "1"
# 改稿時也要用工具自檢：實測第一回合有用工具，之後七個回合一次都沒用，一回合只修掉一兩個錯
VERIFY = (" Before answering, call check_shots on your corrected list and keep fixing until it reports no ERROR."
          if USE_TOOLS else "")


def agent_turn(messages: list, model: str, scene: dict) -> tuple:
    """一個回合裡讓 Nemotron 用工具：要量秒數就量、要自檢就自檢，最後交出分鏡 JSON。

    回傳 (最後的文字, 加總的 usage, 工具使用紀錄)。
    工具步數有上限（MAX_TOOL_STEPS）：到了就關掉工具、叫它直接交件 —— 不讓它無限自檢把 token 燒光。
    """
    from . import tools

    msgs, total, log = list(messages), {}, []
    for step in range(MAX_TOOL_STEPS + 1):
        last = step == MAX_TOOL_STEPS
        text, usage, calls = call_with_tools(msgs, model, None if last else tools.SCHEMAS)
        for k, v in (usage or {}).items():
            if isinstance(v, int):
                total[k] = total.get(k, 0) + v
        if not calls:
            return text, total, log
        msgs.append({"role": "assistant", "content": text or "", "tool_calls": calls})
        for c in calls:
            result = tools.run(c["function"]["name"], c["function"]["arguments"], scene)
            log.append({"tool": c["function"]["name"], "result": result[:300]})
            msgs.append({"role": "tool", "tool_call_id": c["id"], "content": result})
    raise ValueError("no final answer after the tool steps")


def duration_table(scene: dict) -> str:
    """每一句台詞最少要幾秒 —— 程式量好直接給模型，不讓它猜。

    實測：第 61 場有 4 個回合在跟「秒數不夠」來回（規則 8）。秒數量得出來
    （gates.speech_seconds：校準過的語速＋動作收尾），交給模型猜只是浪費回合。超過 10 秒的句子標出來要拆。
    """
    rows = []
    for d in scene["dialogue"]:
        need = gates.speech_seconds(d["line"]) + gates.ACTION_TAIL
        flag = "  <- longer than 10 s: split it across shots at a sentence end" if need > gates.MAX_SECONDS else ""
        rows.append(f"- {d['speaker']}: 「{d['line']}」 needs at least {need:.1f} s{flag}")
    return ("Minimum shot length for each line (measured speaking rate + time for the action to land). "
            "A shot holding several lines needs their sum.\n" + "\n".join(rows))


def direct(scene: dict, call=None, model: str = None) -> dict:
    """跑完整個「寫稿 → 關卡 → 改稿」迴圈，回傳 {status, shots, trace}。

    call 可以換成假的（--offline 用），迴圈本身不用打 API 也能測。
    status：passed（零 ERROR）／gave_up（回合用完還有 ERROR，交給人看）。
    """
    for event in direct_iter(scene, call, model):
        if event["type"] == "done":
            return event


FIX_TRIES = 3


def _strip_sound_clauses(shots: list, shot_id: str) -> list | None:
    """把這一鏡表演句裡含會出聲的字（gates.SOUND_WORDS／SPEAK_WORDS）的那一段刪掉；刪完沒東西剩就回 None（交給模型）。
    一段＝用「，」「；」切開的一小句；「之後／然後／最後」這類接詞跟著留在前面。"""
    import copy
    out = copy.deepcopy(shots)
    s = next((x for x in out if x.get("shot_id") == shot_id), None)
    if not s or not s.get("visual_prompt"):
        return None
    bad = re.compile(gates.SOUND_WORDS + "|" + gates.SPEAK_WORDS)
    parts = [p for p in re.split(r"(?<=[，；,;])", s["visual_prompt"]) if p]
    keep = [p for p in parts if not bad.search(p)]
    if not keep or len(keep) == len(parts):
        return None
    s["visual_prompt"] = re.sub(r"[，；,;]\s*$", "。", "".join(keep).strip())
    # 英文版刪掉講嘴、出聲的那一段（給人看的翻譯，跟中文一起改，不然兩版對不上）
    if s.get("visual_prompt_en"):
        en_bad = re.compile(r"\b(mouth|lips?|bites?|breath\w*|sigh\w*|laugh\w*|smil\w*|giggl\w*|speak\w*|talk\w*|says?)\b", re.I)
        en_parts = [p for p in re.split(r"(?<=,)", s["visual_prompt_en"]) if p]
        en_keep = [p for p in en_parts if not en_bad.search(p)]
        if en_keep and len(en_keep) < len(en_parts):
            s["visual_prompt_en"] = re.sub(r",\s*$", ".", "".join(en_keep).strip())
    return out


def fix_shot(scene: dict, shots: list, shot_id: str, call=None, model: str = None) -> dict:
    """只修被生成前關卡擋下的那一鏡：整場目前這一版＋這一鏡的錯 → 改稿（只交改動的鏡）→ 重跑關卡。

    為什麼要有：第三世試跑，前 20 鏡有 5 鏡被生成前關卡擋（短句、不到 5 秒、動作沒起點終點），
    畫面叫使用者「先改分鏡再生」，app 卻沒有任何地方能改一鏡 —— 唯一的路是整段重導（71 分鐘）。
    跟導演迴圈同一套：改稿用 apply_patch（沒交的鏡碰不到），整場錯誤變多就不收。
    可以動前後鄰鏡（短句要併進前一鏡或後一鏡，字不改），併掉的鏡放 remove。
    回傳 {status: fixed/partial/failed, shots, changed, removed, errors_before, errors_after, tries}。
    """
    if not model:
        from ..services import settings_store
        model = settings_store.load()["roles"]["director"]["model"]
    if call is None:
        call = (lambda msgs: agent_turn(msgs, model, scene)[:2]) if USE_TOOLS else (lambda msgs: call_token_factory(msgs, model))
    mine = lambda iss: [i for i in iss if i["level"] == "ERROR" and i["shot"] == shot_id]  # noqa: E731
    issues = gates.check(scene, shots)
    total0, target0 = sum(i["level"] == "ERROR" for i in issues), mine(issues)
    if not target0:
        return {"status": "fixed", "shots": shots, "changed": [], "removed": [], "errors_before": 0, "errors_after": 0, "tries": 0}
    # 只有「表演句寫了會出聲的字」→ 程式直接把那一小段刪掉，不叫模型（範例 3 s002_11「把右手放在嘴邊」：
    #    請 Nemotron 修三次，三次都順手改了鄰鏡的台詞、整場變糟被退 —— 刪一個子句的事，不該交給會亂動的模型）
    if all(i["rule"] == "rule 3" for i in target0):
        fixed = _strip_sound_clauses(shots, shot_id)
        if fixed is not None:
            after = gates.check(scene, fixed)
            if not mine(after) and sum(i["level"] == "ERROR" for i in after) <= total0:
                return {"status": "fixed", "shots": fixed, "changed": [shot_id], "removed": [], "errors_before": total0,
                        "errors_after": sum(i["level"] == "ERROR" for i in after), "tries": 0}
    base = [
        {"role": "system", "content": system_prompt(scene.get("scene_id", ""))},
        {"role": "user", "content": _part_first(scene) + "Direct this scene:\n" + json.dumps(scene, ensure_ascii=False, indent=1) + "\n\n" + duration_table(scene)},
        {"role": "assistant", "content": json.dumps({"shots": shots}, ensure_ascii=False)},
    ]
    note = (f"Shot {shot_id} is blocked by the pre-render gate:\n" + gates.summarize(target0) +
            f"\n\nFix ONLY {shot_id}. You may also change the shot right before or after it when the fix needs it "
            "(e.g. a line too short to stand alone goes into the neighbouring shot together with that shot's line — "
            "keep every word of both lines). If you merge it away, list it in \"remove\". "
            'Return {"changes": [only the shots you changed, complete], "remove": [shot ids]} and nothing else.')
    best, tries = None, 0
    for tries in range(1, FIX_TRIES + 1):
        try:
            reply, _ = call(base + [{"role": "user", "content": note}])
            data = parse_json(reply)
            if "changes" not in data and isinstance(data.get("shots"), list):
                data = {"changes": data["shots"]}
            # 鏡號寫法不一（s003_019 ⇄ s003_19）：實測模型會補零，對不上就變成新增一鏡、原鏡還在 → 格式的事程式改
            fix_id = lambda x: re.sub(r"^(s\d{3})_0*(\d{2,})", lambda m: f"{m.group(1)}_{int(m.group(2)):02d}", str(x or ""))  # noqa: E731
            for c in data.get("changes") or []:
                if isinstance(c, dict) and c.get("shot_id"):
                    c["shot_id"] = fix_id(c["shot_id"])
            data["remove"] = [fix_id(x) for x in data.get("remove") or []]
            # 併鏡卻忘了寫 remove（實測：「にゃあ」併進前一鏡、原鏡還在 → 同一句講兩次、整場變糟被退）：
            #    改過的鏡已經包含這一鏡的每一句 → 程式替它刪掉原鏡
            said = lambda s: [d.get("text", "") for d in s.get("dialogue_lines") or []] or ([s["dialogue"]] if s.get("dialogue") else [])  # noqa: E731
            mine_lines = said(next(s for s in shots if s.get("shot_id") == shot_id))
            if mine_lines and shot_id not in {c.get("shot_id") for c in data.get("changes") or []} and any(
                    all(t in said(c) for t in mine_lines) for c in data.get("changes") or [] if isinstance(c, dict)):
                data["remove"] = list({*(data.get("remove") or []), shot_id})
            for c in data.get("changes") or []:     # 併鏡時 dialogue 常留空：照規則 6 由程式接起來（格式的事不叫模型做）
                if isinstance(c, dict) and c.get("dialogue_lines") and not c.get("dialogue"):
                    c["dialogue"] = "".join(d.get("text", "") for d in c["dialogue_lines"])
            new = apply_patch(shots, data)
        except (ValueError, json.JSONDecodeError) as e:
            note = note.split("\n\nYour last reply")[0] + f"\n\nYour last reply could not be used: {e}. Return only the JSON object."
            continue
        iss = gates.check(scene, new)
        total, target = sum(i["level"] == "ERROR" for i in iss), mine(iss)
        removed = shot_id in {s.get("shot_id") for s in shots} - {s.get("shot_id") for s in new}
        # 收不收看「有沒有改出新錯」，不是看總數：實測，刪掉被擋的鏡（-1）、鄰鏡多一個錯（+1），
        #    總數持平就被收下 —— 其實只是把擋下的地方往前移一鏡
        #    比對時拿掉數字與括號（「重複 4 鏡 (s002_04, …)」刪一鏡就變成 3 鏡，不是新錯）
        key = lambda i: (i["shot"], i["rule"], re.sub(r"\(.*?\)|\d+", "", i["msg"]))  # noqa: E731
        had = {key(i) for i in issues}
        fresh = [i for i in iss if i["level"] == "ERROR" and key(i) not in had]
        logger.warning("fix_shot %s try %d: changed=%s removed=%s target_left=%d new_errors=%s", shot_id, tries,
                    [c.get("shot_id") for c in data.get("changes") or []], data.get("remove"), len(target),
                    [(i["shot"], i["rule"], i["msg"][:80]) for i in fresh][:5])
        if not fresh and (removed or len(target) < len(target0)):
            best = (new, data, total, target)
            if not target or removed:
                break
        # 只講「這次改出來的新錯」：整場原本就有的錯一起列，模型分不出哪個是它造成的
        note = note.split("\n\nYour last reply")[0] + ("\n\nYour last reply did not fix it" + (
            f" and made the scene worse ({total0} → {total} errors)" if total > total0 else "") + ". " +
            ("It introduced:\n" + gates.summarize(fresh[:8]) if fresh else "Still blocked:\n" + gates.summarize(target[:8])))
    if not best:
        return {"status": "failed", "shots": shots, "changed": [], "removed": [], "errors_before": len(target0),
                "errors_after": len(target0), "tries": tries}
    new, data, total, target = best
    return {"status": "partial" if target else "fixed", "shots": new,
            "changed": [s["shot_id"] for s in data.get("changes") or []], "removed": list(data.get("remove") or []),
            "errors_before": len(target0), "errors_after": len(target), "tries": tries}


def _part_first(scene: dict) -> str:
    """長場次切段導的那一句（services/direction.split_scene 的 part_note）放在最前面：埋在 JSON 裡會被當成普通欄位，
    範例 3 第 2 場第 2 段照樣當成一場的開頭，補了兩鏡「月音揮手歡迎 Lisa 走進來」。"""
    note = scene.get("part_note")
    return f"⚠ {note} Do not add an opening or introduction beat unless this is the opening part.\n\n" if note else ""


def direct_iter(scene: dict, call=None, model: str = None, record: bool = True):
    """導演迴圈（_direct_iter）外面包一層：每一場結束，不管哪一個出口，都寫進學習記憶庫。

    包在外面而不是在五個出口各寫一次：出口有五個，漏一個就少一筆紀錄，而少了紀錄就學不到
    （規則要長在手落下的地方 —— 這裡就是每一場都一定會經過的地方）。
    record=False 給假模型的測試用，免得測試資料混進真正的成績單。
    """
    for ev in _direct_iter(scene, call, model):
        if ev["type"] == "done":
            try:
                from . import learning
                ev["hints_given"] = bool(learning.mistakes_brief())
                if record:
                    learning.record(scene["scene_id"], ev)
            except Exception as e:      # 記憶庫寫不進去不能讓導演的結果跟著不見
                ev["learning_error"] = str(e)[:200]
        yield ev


def _watch(shots) -> str:
    """改稿意見後面附上「已經用在兩鏡的表演句」（gates.near_repeats），沒有就不附。"""
    w = gates.near_repeats(shots or [])
    return "\n\n" + w if w else ""


def _direct_iter(scene: dict, call=None, model: str = None):
    """同一個迴圈，但每一回合一結束就 yield 出來 —— 示範網頁靠它一回合一回合即時顯示。

    事件：{"type": "round", ...這一回合的紀錄, "shots_detail": [...]}，最後一個 {"type": "done", status, shots, trace}。
    """
    if not model:
        from ..services import settings_store
        model = settings_store.load()["roles"]["director"]["model"]     # 設定頁「分鏡（導演）」那一格
    # 真的模型：每一回合是一個「會用工具的回合」（agent_turn）。測試傳進來的假模型照舊一問一答。
    tool_log = []
    if call is None and USE_TOOLS:
        def call(msgs):
            text, usage, log = agent_turn(msgs, model, scene)
            tool_log[:] = log
            return text, usage
    call = call or (lambda msgs: call_token_factory(msgs, model))
    # 每一回合只帶「規則＋劇本＋最新一版＋這一回合的意見」，不把每一版都疊進對話：
    # 實測疊到第七回合，對話太長，模型光思考就把 32000 tokens 用完、回覆是空的。
    # 開工前先看自己的成績單：過去每一場第一回合最常被哪幾條擋（learning.py）。
    # 沒有紀錄（第一次跑、或讀不到記憶庫）就不提醒，不編。
    try:
        from . import learning
        brief = learning.mistakes_brief()
    except Exception:
        brief = ""
    base = [
        {"role": "system", "content": system_prompt(scene.get("scene_id", ""))},
        {"role": "user", "content": _part_first(scene) + "Direct this scene:\n" + json.dumps(scene, ensure_ascii=False, indent=1)
                                    + "\n\n" + duration_table(scene) + ("\n\n" + brief if brief else "")},
    ]
    trace, shots, critiques, best, current = [], [], 0, None, None
    low, stall = 10**9, 0                                    # 目前最少的錯誤數、幾回合沒創新低
    cur_errors, cur_feedback, fresh_phase = 10**9, "", True   # 目前這一版的錯誤數；評審後的第一回合 fresh_phase=True
    last_reply, note = None, None
    # 回合預算分階段：第一次過關卡最多 MAX_ROUNDS 回合；每次評審之後，另外給 REVISION_ROUNDS 回合改。
    # 雲端實測：過關卡就用了 7 回合，評審意見只剩 2 回合可改、沒改完就退回評審前那版 —— 評審等於白評。
    rnd, budget = 0, MAX_ROUNDS
    while rnd < budget:
        rnd += 1
        messages = base + ([{"role": "assistant", "content": last_reply}, {"role": "user", "content": note}] if last_reply else [])
        t0 = time.time()
        try:
            reply, usage = call(messages)
        except ValueError as e:
            # 回覆是空的（思考吃光額度）：這一回合算失敗、提醒它別想太久，不讓整場崩掉
            step = {"round": rnd, "model": model, "seconds": round(time.time() - t0, 1), "usage": {}, "shots": 0,
                    "errors": 1, "warnings": 0, "issues": [{"shot": "reply", "level": "ERROR", "rule": "format", "msg": str(e)}]}
            trace.append(step)
            yield {"type": "round", **step, "shots_detail": shots}
            if last_reply:
                note = note.split("\n\nNote:")[0] + ("\n\nNote: your last attempt ran out of tokens while thinking. "
                                                     "Keep the reasoning short and return the JSON directly.")
            continue
        step = {"round": rnd, "model": model, "seconds": round(time.time() - t0, 1), "usage": usage,
                "tools": [x["tool"] for x in tool_log], "tool_log": list(tool_log)}
        tool_log.clear()
        try:
            data = parse_json(reply)
            if current and isinstance(data, dict) and "changes" not in data and \
                    len(data.get("shots") or []) < len(current) // 2:
                # 它交了 {"shots": [只有改的那幾鏡]}：意思是改稿，不是新的一整場。
                # 實錄：第 3 回合交 2 鏡，被當成「整場只剩 2 鏡」，錯誤數 17 < 18 還被收下當新的底。
                data = {"changes": data["shots"]}
            if current and isinstance(data, dict) and "changes" in data:
                shots = apply_patch(current, data)          # 改稿：只交改動的鏡，合併回目前這一版
            else:
                shots = parse_shots(reply)                   # 初稿（或它堅持交整份）
            issues = gates.check(scene, shots)
            feedback = gates.summarize(issues)
            errs = sum(i["level"] == "ERROR" for i in issues)
            step.update(shots=len(shots), errors=errs,
                        warnings=sum(i["level"] == "WARN" for i in issues), issues=issues)
            # 改壞了就不收：修關卡的階段，錯誤變多的那一版不當下一回合的底，退回錯最少的那一版。
            # 雲端實測：錯誤一路 13→11→13、5→3→3→… 來回跳，因為改壞的版本照樣被拿來當下一回合的底。
            # 評審意見後的第一回合例外（本來就要照意見大改），它改完就是新的底。
            #    空的分鏡也不收（第三世：最後一回合回了 0 鏡、9 個「台詞漏掉」，錯誤數比上一版少就被當成更好的版本）
            if current and ((not fresh_phase and errs > cur_errors) or not shots):
                step["rejected"] = True
                feedback = (f"Your revision made it worse ({cur_errors} → {errs} errors) and was NOT applied; the shot list "
                            "below is still the previous version. Undo what broke and fix only these remaining errors:\n"
                            + cur_feedback + "\n\nYour rejected revision introduced:\n" + feedback)
                shots = current
            else:
                current, cur_errors, cur_feedback = shots, errs, feedback
            fresh_phase = False
        except (ValueError, json.JSONDecodeError) as e:
            feedback = f"Your reply could not be parsed: {e}. Return only the JSON object."
            step.update(shots=0, errors=1, warnings=0, issues=[{"shot": "reply", "level": "ERROR", "rule": "format", "msg": str(e)}])
        trace.append(step)
        yield {"type": "round", **step, "shots_detail": shots}
        if step["errors"] and step.get("shots"):
            low, stall = (step["errors"], 0) if step["errors"] < low else (low, stall + 1)
            if stall >= PATIENCE:
                break
        if step["errors"] == 0:
            best = (rnd, shots)          # 最後一版「關卡全過」的分鏡：評審是加分，不能因為改壞了反而什麼都沒有
            # 關卡全過 → 交給評審（Ultra）。評審點頭、或評審次數用完，才算完成。
            # 評審的意見回到寫分鏡的人手上，改完一樣要重新過關卡 —— 評審不能繞過關卡，關卡也不能取代評審。
            if critiques >= CRITIC_ROUNDS:
                yield {"type": "done", "status": "passed", "rounds": rnd, "shots": shots, "trace": trace}
                return
            from . import critic
            t1 = time.time()
            c = critic.critique(scene, shots)
            critiques += 1
            cstep = {"round": rnd, "type": "critique", "model": c["_meta"]["model"], "seconds": round(time.time() - t1, 1),
                     "verdict": c.get("verdict"), "overall": c.get("overall"), "notes": c.get("notes") or [], "usage": c["_meta"]["usage"],
                     "dropped_notes": c.get("dropped_notes") or [],
                     **({"raw_excerpt": c["raw_excerpt"]} if c.get("raw_excerpt") else {})}
            trace.append(cstep)
            yield cstep
            if c.get("verdict") == "unreviewed":
                # 評審壞掉 ≠ 評審核准：交出去，但明講「沒人看過品質」
                yield {"type": "done", "status": "passed_unreviewed", "rounds": rnd, "shots": shots, "trace": trace,
                       "note": "gates passed, but the producer's review failed — needs a human look"}
                return
            if c.get("verdict") == "approve":
                yield {"type": "done", "status": "passed", "rounds": rnd, "shots": shots, "trace": trace}
                return
            # 只改評審點名的鏡，其他鏡一字不動 —— 實測整份重寫會「修一個壞一個」，評審後兩回合都沒再過關卡
            # 同一個修法不要套到每一鏡：實測評審說「Lisa 要閃躲」，Super 就把「Lisa 看向門口」貼進五鏡，
            # 被「同一句表演抄三鏡」關卡擋回去、回合用完。反應要隨著衝突一路升級。
            last_reply, note = reply, (critic.notes_text(c) + "\n\nRevise ONLY the shots these notes name (you may insert a new "
                                       "shot where a note asks for one). Copy every other shot exactly as it is — they already pass "
                                       "the gates. Do not paste the same fix into every shot: a reaction must change as the scene "
                                       "escalates (e.g. first looks away, later steps back, later grips the sink), because the gates "
                                       "reject any action repeated in 3 or more shots. Keep everything the gates require." + VERIFY + PATCH_ASK
                                       + _watch(current))
            # 對話裡的「上一版」放合併好的完整分鏡，不放它交的改動片段 —— 它要看得到現在整場長怎樣
            last_reply = json.dumps({"shots": current}, ensure_ascii=False)
            budget = rnd + REVISION_ROUNDS
            fresh_phase = True                       # 評審後的第一回合：照意見大改，不跟上一版比錯誤數
            continue
        if current:
            last_reply = json.dumps({"shots": current}, ensure_ascii=False)
            note = ("The pre-render gates rejected this shot list. Fix every ERROR — change only the shots the errors "
                    "point at. Fix WARNs too unless the shot genuinely needs it." + VERIFY + PATCH_ASK + "\n\n" + feedback
                    + _watch(current))
        else:   # 連一份能解析的初稿都還沒有：照舊要整份
            last_reply, note = reply, ("Your reply could not be used. Return the full shot list JSON.\n\n" + feedback)
    if best:
        # 評審之後的修改沒能在回合內過關卡 → 交出最後一版過關的，評審意見留在紀錄裡給人看
        yield {"type": "done", "status": "passed", "rounds": best[0], "shots": best[1], "trace": trace,
               "note": "revisions after the producer's notes did not pass the gates in time; keeping the last version that did"}
        return
    # 回合用完沒過關卡：交錯誤最少的那一版（current），不是最後一回合 —— 最後一回合可能剛好是空的
    yield {"type": "done", "status": "gave_up", "rounds": rnd, "shots": current or shots, "trace": trace}


if __name__ == "__main__":
    scene = json.load(open(sys.argv[1], encoding="utf-8"))
    fake = None
    if "--offline" in sys.argv:
        ref = (ROOT / "examples" / f"{scene['scene_id']}_reference_shots.json").read_text(encoding="utf-8")
        fake = lambda msgs: ("<think>offline</think>" + ref, {})
    out = direct(scene, call=fake)
    for s in out["trace"]:
        print(f"round {s['round']}: {s['shots']} shots, {s['errors']} errors, {s['warnings']} warnings, {s['seconds']}s")
    print(out["status"])
    (ROOT / "outputs").mkdir(exist_ok=True)
    dest = ROOT / "outputs" / f"{scene['scene_id']}_director.json"
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("saved", dest)
