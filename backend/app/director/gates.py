"""確定性關卡：導演（Nemotron）寫出來的分鏡，送去渲染前必須全部通過。

為什麼是程式不是模型：這些都是「量得出來」的錯（秒數、禁字、一鏡兩人講話），
量得出來的東西交給模型判斷，它會偶爾放過；交給程式，每次都擋。
模型負責的是量不出來的那一半 —— 怎麼切鏡、表演怎麼寫、壞了為什麼壞。

每條關卡對應 rules/directing_rules.md 的一條規則，回報時附上規則編號，
導演改稿時才知道「為什麼被擋」，而不是只知道「被擋了」。

用法：python gates.py scenes/s029_first_words.json examples/s029_reference_shots.json
"""

import json
import re
import sys
from difflib import SequenceMatcher

# 語速：在我們 1312×736 的渲染規格下，用 33 支已驗收鏡次的實測語音長度擬合
# （秒 ≈ 0.214 × 唸得出來的字數 + 0.94）。換解析度要重量，不能沿用。
SPEECH_A, SPEECH_B = 0.214, 0.94
ACTION_TAIL = 1.5          # 台詞講完後，動作收尾需要的時間
MAX_SECONDS, MIN_SECONDS = 10, 5

SHOT_TYPES = {"WIDE", "MEDIUM", "MEDIUM_CLOSE", "CLOSE_UP"}
CAMERA_MOVES = {"STATIC", "BREATHE", "DOLLY_IN", "DOLLY_OUT", "PUSH_FACE", "PUSH_RETURN", "ZOOM", "PULL_FOCUS",
                "PAN_RETURN", "TILT_UP", "TILT_DOWN", "TRACK", "FOLLOW"}
VOICE_ROLES = {"character", "offscreen", "narration", "none"}
# 演得像在講話的動作（畫外音鏡出現就擋，見規則 6）
_TALK_GESTURES = ("張開雙手", "攤手", "攤開雙手", "比劃", "比手畫腳", "解釋", "說明", "講話", "說話", "開口", "點額頭", "手勢")

SPEAK_WORDS = r"講|開口|說話|說完|把話"
SOUND_WORDS = r"嘴|唇|抿|咬|笑|呼吸|吸.{0,3}氣|嘆.{0,3}氣|呼.{0,3}氣|懶腰|哈欠|清喉嚨|咳|哼|哭泣|大哭|哭出|哭了起來|哭喊|抽泣|啜泣|嗚咽"
NEGATION_WORDS = r"不要|沒有|不准|禁止|別讓|不再"
STEP_WORDS = r"之後|然後|接著|最後"
BODY_WORDS = r"頭|手|身|肩|腿|膝|靠|站|坐|走|拿|放|轉|抱|撐|傾|伸|舉|推|敲|撥|翻|縮|收|擦|搭|捲|趴|跑|步|逼近|退|蹲|跪|彎"


RETURN_MOVES = {"PUSH_RETURN", "PAN_RETURN"}
NEAR_SHOTS = {"CLOSE_UP", "MEDIUM_CLOSE"}
HAND_WORDS = re.compile(r"手|掌|指|握|拳|hand|finger|palm|fist", re.I)
FACE_WORDS = re.compile(r"臉|眼|淚|鏡|頭髮|髮|嘴|額|耳|face|eye|tear|glasses|hair|mouth", re.I)


def _low_hands(prompt: str) -> bool:
    """有沒有「肩膀以下」的手部動作：逐個子句看，有手、但同一句沒有臉部的東西（推眼鏡、擦眼淚、撥頭髮在臉旁邊，拍得到臉，不算）。

    拿正式製作第 61 場的人寫分鏡校準：按胸口、雙手交叉要叫；推眼鏡、擦眼淚不該叫。
    """
    return any(HAND_WORDS.search(c) and not FACE_WORDS.search(c)
               for c in re.split(r"[，、。；,;]|之後|然後|最後", prompt))


def _local_engine() -> bool:
    """設定頁選的是本機引擎（不給首幀）嗎？讀不到設定就當本機（Greenlight Local 的預設）。"""
    try:
        from ..services import settings_store

        return settings_store.load().get("engine", "local") == "local"
    except Exception:  # noqa: BLE001 - 關卡不能因為讀設定失敗就整個不跑
        return True


def spoken_len(line: str) -> int:
    """唸得出來的字數：中文一字一個；連續的拉丁字母整段算「長度 ÷ 2、至少 1」。

    為什麼不直接算字元：「Luna」4 個字元只唸兩個音節。照字元算會把秒數估太長，
    多出來的空檔，渲染模型會自己編一句話填進去（實測過）。
    """
    n, i = 0, 0
    while i < len(line):
        c = line[i]
        if c.isascii() and c.isalnum():
            j = i
            while j < len(line) and line[j].isascii() and line[j].isalnum():
                j += 1
            n += max(1, (j - i) // 2)
            i = j
        else:
            if c.strip() and not re.match(r"[，。！？、…；：「」『』,.!?~\-]", c):
                n += 1
            i += 1
    return n


def speech_seconds(line: str) -> float:
    """一句台詞實際要唸多久（不含動作收尾）。"""
    return SPEECH_A * spoken_len(line) + SPEECH_B if line else 0.0


def _norm(s: str) -> str:
    return re.sub(r"[\s，。！？、…；：「」『』,.!?~\-（）()]", "", s or "")


def _coverage(line: str, text: str) -> float:
    """劇本台詞有幾成的字，照順序出現在分鏡台詞裡。"""
    if not line:
        return 1.0
    m = SequenceMatcher(None, line, text, autojunk=False)
    return sum(b.size for b in m.get_matching_blocks()) / len(line)


def _pre_speech_face_only(prompt: str) -> bool:
    """開口前是不是只有臉部微動作（眨眼、視線、閉眼）—— 那些不佔秒數。

    比對前先拿掉姿勢狀態（站在／坐在…）和位置詞（身邊／身上…）：
    「站在水面上」是狀態不是動作，「身邊」的「身」也不是身體。
    """
    segs = [x for x in re.split(r"[，。；]|" + STEP_WORDS, prompt) if x.strip()]
    if len(segs) < 2:
        return False
    def has_body(seg):
        seg = re.sub(r"(站|坐|躺|趴)在|身[邊旁後前上]", "", seg.replace("眉頭", "眉"))
        return re.search(BODY_WORDS, seg)
    return not any(has_body(x) for x in segs[:-1])


def check(scene: dict, shots: list, allow_waivers: bool = False) -> list:
    """回傳問題清單；每一項 {shot, level, rule, msg}。level=ERROR 必須改，WARN 要嘛改、要嘛講出理由。

    allow_waivers：只有人寫的分鏡（examples/ 的對照組）才可以帶 `_waive` 豁免某條規則，而且要寫理由
    （例：鏡子裡的倒影本來就要跟著對嘴 —— 正式製作刻意這樣寫）。導演模型寫的分鏡一律不吃豁免：
    讓模型自己豁免自己，關卡就形同虛設。
    """
    issues = _check(scene, shots)
    if allow_waivers:
        waived = {(s.get("shot_id"), w["rule"]): w["reason"] for s in shots for w in s.get("_waive") or []}
        for i in issues:
            reason = waived.get((i["shot"], i["rule"]))
            if reason and i["level"] == "ERROR":
                i.update(level="WARN", msg=f"waived by the human director ({reason}): {i['msg']}")
    return issues


SIZE_RANK = {"WIDE": 0, "MEDIUM": 1, "MEDIUM_CLOSE": 2, "CLOSE_UP": 3}

# ---- 規則 28～30：照搬正式管線 作者管線的 rulecheck.py（判斷式與輕重都照它；要改先改那邊的實證）----
TEAR_DROP = r"一滴.{0,2}淚|一顆.{0,2}淚|淚珠|大滴|一滴一滴|水珠"
LITERAL_TRAPS = [
    ("ERROR", r"眼裡|眼睛裡|心裡|腦海",
     "poetic 眼裡/心裡 gets drawn literally (light glowing inside the eye) — describe what is visible"),
    ("ERROR", TEAR_DROP,
     "a 'drop' of tears is drawn as one big bead stuck on the face — write 一道細細的淚痕順著臉頰流下來 (tears welling in the eyes are fine)"),
]
DUP_NOUNS = ("書桌", "床", "落地窗", "椅", "檯燈", "書架", "筆電", "手機", "湯碗", "微波爐", "毛衣", "圍巾", "大衣", "盆栽")
START_POS = r"站在|坐在|躺在|趴在|靠在|蹲在|走在|並肩|背對|面對|面向|正對|側對|在[^，。胸臉耳頭手腰膝]{1,12}(前|旁|邊|裡|上|下|中間|入口)"
_STATE_V = r"削|切|剝|撕|剪|摺|寫|塗|攪|拌|揉|吃|喝|嚼|啃"
_DONE = r"完|斷|好|成|掉|下來|下去|起來|進|到|開|出|兩下|幾下|一下|一刀|一口|一塊|一片|一半"


def _no_end(act: str) -> list[str]:
    """改變東西狀態的動作寫成持續式或沒寫結果、或有「繼續／一直／不停」→ 模型來回重做（削掉又長回去，正式管線 s024_01）。"""
    out = []
    for seg in [x for x in re.split(r"[，。；]|之後|然後|接著|最後", act) if x.strip()]:
        if re.search(r"繼續|一直|不停|不斷|反覆|來回|持續", seg):
            out.append(seg)
            continue
        for m in re.finditer(_STATE_V, seg):
            rest = seg[m.end():]
            if rest.startswith("著") or not (rest.startswith("了") or re.search(_DONE, rest[:12])):
                out.append(seg)
                break
    return out


ENTER_WORDS = r"走出來|走進|進來|走出|出來|入鏡|走到.{0,6}(旁|身)"


def chain_problem(prev_type: str, cur_type: str, prev_chars, cur_chars, prompt: str = "") -> str | None:
    """接上一鏡的末幀會不會壞；會的話回傳原因（bridge 標接幀、生成前關卡都用這一個）。

    ① 景別差兩級以上 → 模型撐約 1.5 秒就硬切成另一個構圖（正式管線 rulecheck 三；s061_12：中特寫接全景，不到一秒就切）
    ② 這一鏡要的人不在上一鏡 → 末幀裡沒有她，模型硬切到遠景重編一個場面（s061_16 第三版：多出第三個人）
    """
    jump = abs(SIZE_RANK.get(cur_type, 1) - SIZE_RANK.get(prev_type, 1))
    if jump >= 2:
        return f"chained from a {prev_type} end frame into a {cur_type} shot — a two-step size jump hard-cuts after ~1.5 s"
    missing = set(cur_chars or []) - set(prev_chars or [])
    # 寫了走進來（s061_01b：月音從隔間走出來）＝本來就要入鏡，不是缺人
    if missing and not re.search(ENTER_WORDS, prompt or ""):
        return f"chained from an end frame without {', '.join(sorted(missing))} — the model cuts away to invent them"
    return None


def check_shot(scene: dict, shots: list, shot_id: str) -> list:
    """生成前只看「這一鏡」的問題（整場的統計、劇本覆蓋率這種不歸單鏡管的不算）。

    為什麼要有：關卡原本只在導演迴圈裡跑，人在畫面上改的、手改的分鏡完全不經檢查就送去生成
    （補跑第 61 場：手改的鏡 5 鏡踩規則 3、15 鏡的台詞尾巴被切掉）。規則要長在手落下的地方。
    """
    return [i for i in check(scene, shots) if i["shot"] == shot_id]


def _check(scene: dict, shots: list) -> list:
    issues = []
    def add(shot, level, rule, msg):
        issues.append({"shot": shot, "level": level, "rule": rule, "msg": msg})

    chars = scene["characters"]
    local = _local_engine()
    by_id = {s.get("shot_id"): s for s in shots}
    for s in shots:
        sid = s.get("shot_id", "?")
        # 本機引擎 起會接上一鏡末幀：有接的鏡就有首幀，「沒首幀」那幾條限制不適用
        no_first_frame = local and not s.get("first_frame_from")
        src = by_id.get(s.get("first_frame_from"))
        if src:
            why = chain_problem(src.get("shot_type"), s.get("shot_type"),
                                src.get("characters_in_frame"), s.get("characters_in_frame"), s.get("visual_prompt"))
            if why:
                add(sid, "ERROR", "rule 27", f"{why} — match the shot size and cast of {src.get('shot_id')}, "
                                             "or don't chain and borrow the end frame of an approved shot with the same blocking")
        prompt = s.get("visual_prompt") or ""
        line = s.get("dialogue") or ""
        dur = s.get("duration_seconds") or 0
        in_frame = s.get("characters_in_frame") or []
        role = s.get("voice_role") or ("character" if line else "none")

        # 欄位與列舉
        if s.get("shot_type") not in SHOT_TYPES:
            add(sid, "ERROR", "enums", f"shot_type must be one of {sorted(SHOT_TYPES)}")
        if s.get("camera_move") not in CAMERA_MOVES:
            add(sid, "ERROR", "enums", f"camera_move must be one of {sorted(CAMERA_MOVES)}")

        # 本機引擎不給首幀（只給人物卡＋空景卡），本機管線規則庫兩條沒首幀的限制：
        #   「去而復返」的運鏡回不來（00_症狀表：s016_04 兩版都只推近不退回，對照組有首幀的都回得來）；
        #   近景寫肩膀以下的手部動作，鏡頭會跟去拍手／軀幹、臉不見（00_症狀表：s024_04／08）。
        if no_first_frame:
            if s.get("camera_move") in RETURN_MOVES:
                add(sid, "ERROR", "no first frame",
                    f"{s.get('camera_move')} returns to its start, but the local engine gets no first frame (reference "
                    "cards only) so the camera has no start to return to — use DOLLY_IN, PUSH_FACE, BREATHE or STATIC")
            if s.get("shot_type") in NEAR_SHOTS and _low_hands(prompt):
                add(sid, "WARN", "no first frame",
                    "close shot without a first frame acting with the hands: the camera follows the hands and loses "
                    "the face — act it with the head and shoulders (look up/down, eyes, shoulders drop), or explain why")
        if role not in VOICE_ROLES:
            add(sid, "ERROR", "enums", f"voice_role must be one of {sorted(VOICE_ROLES)}")
        unknown = [c for c in in_frame if c not in chars]
        if unknown or not in_frame:
            add(sid, "ERROR", "cast", f"characters_in_frame must be non-empty and from {list(chars)}; got {in_frame}")

        # 規則 10：各自待在自己的世界，不同地點的人不同框
        locs = {chars[c].get("location") for c in in_frame if c in chars}
        if len(locs) > 1:
            add(sid, "ERROR", "rule 10", f"characters from different locations share a frame: {in_frame}")

        # 規則 15：站位決定場景行，而且站位要在角色所在的地點
        spots = scene.get("render_zh", {}).get("spots", {})
        if spots:
            sp = spots.get(s.get("spot"))
            if not sp:
                add(sid, "ERROR", "rule 15", f"spot must be one of {list(spots)}")
            elif locs and sp["location"] not in locs:
                add(sid, "ERROR", "rule 15", f"spot {s.get('spot')!r} is in {sp['location']}, but the characters are in {sorted(locs)}")
            elif s.get("shot_type") == "WIDE" and s.get("spot") != "room_wide" and "room_wide" in spots and sp["location"] == "luna_room":
                add(sid, "WARN", "rule 15", "a WIDE shot of the room should use the room_wide spot")

        # 規則 6：誰講話要清楚。
        #   一個人講 → 講的人要在畫面裡；畫面外的聲音（offscreen）→ 講的人**不能**在畫面裡（不然模型讓畫面裡的人對嘴）。
        #   同框兩人一來一往 → 用 dialogue_lines，一人一行、照講話順序，每個講話的人都要在畫面裡
        #   （正式製作 同框對話成功的寫法；舊規則「一鏡只能一個講話者」是舊寫法的限制，不是模型的限制）。
        dl = s.get("dialogue_lines") or []
        if dl:
            if "".join(x.get("text", "") for x in dl) != line.replace(" ", "") and _norm("".join(x.get("text", "") for x in dl)) != _norm(line):
                add(sid, "ERROR", "rule 6", "dialogue must be the dialogue_lines joined in order")
            for x in dl:
                if x.get("speaker") not in in_frame:
                    add(sid, "ERROR", "rule 6", f"{x.get('speaker')!r} speaks in dialogue_lines but is not in the frame")
        elif line and role == "character" and s.get("speaker") not in in_frame:
            add(sid, "ERROR", "rule 6", f"speaker {s.get('speaker')!r} must be on screen for an on-screen line")
        if line and role == "offscreen" and s.get("speaker") in in_frame:
            add(sid, "ERROR", "rule 6", f"an off-screen voice cannot belong to someone in the frame ({s.get('speaker')!r})")
        # 畫外音的鏡頭，畫面裡的人不能「看起來在講話」：模型把聲音配給演得像講話的那個人
        #   （範例 2 s004_03：Lisa 的畫外音，畫面裡深息面對月音、點額頭攤手 → 寫了「Lisa 的聲音」深息對嘴變女聲；
        #    拿掉點名重拍 → 深息直接用男聲把 Lisa 的台詞講掉）。提示詞的字改不掉，要改這一鏡在演什麼。
        if line and role == "offscreen" and in_frame:
            why = [w for w in _TALK_GESTURES if w in prompt]
            # 面對面本身不算（第三世試跑 4 鏡：璃安跟ぷく面對面、沒手勢，畫外音正常）；有手勢才擋，面對面只是補充說明
            if why and len(in_frame) > 1 and s.get("blocking") == "face_to_face":
                why.insert(0, "face to face")
            if why:
                add(sid, "ERROR", "rule 6",
                    f"off-screen voice, but the people on screen look like they are talking ({', '.join(why)}) — the model gives "
                    "the line to whoever acts like a speaker. Have them listen or look at something (still, mouths closed), "
                    "show only the object, or put the speaker in the frame")

        # 規則 21：兩個人同框，表演句要點名誰做什麼 —— 只寫「她」，模型分不出是哪個她
        if len(in_frame) > 1:
            for c in in_frame:
                nm = chars.get(c, {}).get("name_zh")
                if nm and nm not in prompt:
                    add(sid, "ERROR", "rule 21", f"two people in frame: name who does what — {nm!r} is never named in the performance line")
        # 反方向：表演句點了名、人物卻不在畫面清單 → 提示詞的人物欄沒有他（沒參考卡），模型照名字自己編一個
        #    （第三世試跑 s001_01：表演句寫「ぷく在右側緩慢漂浮」，ぷく不在 characters_in_frame → 畫出一隻小白鬼）
        #    整部片的其他角色也算（scene["other_cast"]：這場的角色表沒列他，表演句卻寫到 —— s001_01 的ぷく就是）
        named = [(c, info.get("name_zh") or info.get("name")) for c, info in chars.items()]
        named += [(n, n) for n in scene.get("other_cast") or [] if n not in {x[1] for x in named}]
        for c, nm in named:
            if c not in in_frame and nm and nm in prompt:
                add(sid, "ERROR", "rule 21", f"{nm!r} is named in the performance line but is not in characters_in_frame — "
                                             "add them to the frame or take them out of the line (the model invents a stranger)")

        # 規則 7、8：秒數
        need = speech_seconds(line) + ACTION_TAIL if line else MIN_SECONDS
        if dur > MAX_SECONDS:
            add(sid, "ERROR", "rule 7", f"{dur}s > {MAX_SECONDS}s — split the line across two consecutive shots, never delete words")
        if dur < MIN_SECONDS:
            add(sid, "ERROR", "rule 7", f"{dur}s < {MIN_SECONDS}s minimum")
        if line and dur < need:
            # 語速公式是用中文校準的；日文（有假名）只提醒不擋 —— 第 61 場實測：照公式擋，定案 17 鏡會誤擋 8 鏡
            #    （模型會照鏡頭長度調節奏，短鏡就早開口、講快），真的被切掉的只有 1 鏡。被切掉改由生成後的「尾字沒唸完」抓。
            level = "WARN" if re.search(r"[぀-ヿ]", line) else "ERROR"
            add(sid, level, "rule 8", f"{dur}s is too short: the line needs {speech_seconds(line):.1f}s + {ACTION_TAIL}s to land = {need:.1f}s")
        # 每個寫出來的動作大約吃掉 1.5 秒；超過「台詞＋動作」的部分才算沒人負責的空檔
        #（門檻用人工驗收過的第 29 場校準：走位多的全景鏡不該被誤報）
        slack = 1.5 * len([x for x in re.split(r"[，。；]|" + STEP_WORDS, prompt) if x.strip()])
        if line and dur > need + max(3.5, slack):
            add(sid, "WARN", "rule 2", f"{dur}s leaves {dur - need:.1f}s unowned after the line — the model will fill it; add actions or shorten")

        # 規則 1、3：提到就會畫、寫嘴就會講
        if re.search(SPEAK_WORDS, prompt):
            add(sid, "ERROR", "rule 3", f"performance line mentions speaking ({re.search(SPEAK_WORDS, prompt).group()}) — body actions only, dialogue goes in the dialogue field")
        if re.search(SOUND_WORDS, prompt):
            add(sid, "ERROR", "rule 3", f"performance line mentions a mouth/sound action ({re.search(SOUND_WORDS, prompt).group()}) — the model will make it audible")
        if re.search(NEGATION_WORDS, prompt):
            add(sid, "WARN", "rule 1", f"negation ({re.search(NEGATION_WORDS, prompt).group()}) summons the thing it forbids — describe only what is there")

        # 規則 28：字面陷阱 —— 照搬正式管線 rulecheck 的判斷式與輕重（盤點出 app 缺的那幾條）
        for level, pat, msg in LITERAL_TRAPS:
            if re.search(pat, prompt) and not (pat == TEAR_DROP and re.search(r"淚.{0,2}(落|滴)(在|到)", prompt)):
                add(sid, level, "rule 28", msg)
        if line and role == "character" and re.search(r"喝|吃|含著|嚼", prompt):
            add(sid, "WARN", "rule 28", "the speaker is drinking/eating while talking — the mouth can't do both")
        if "！" in line or "!" in line:
            add(sid, "WARN", "rule 28", "'!' in the line pitches the voice up an octave and slurs it — use 、/。 and let particles carry the emotion")
        if line and spoken_len(line) <= 3:
            # 改字會違反規則 9（照抄原文），所以修法是併鏡：跟前後那句放同一鏡（「にゃあ」「塔列昂」這種原文就很短）
            add(sid, "ERROR", "rule 28", f"line of {spoken_len(line)} characters — H3 garbles lines this short; put it in the same shot as the line before or after it (keep the words)")
        elif line and spoken_len(line) <= 8:
            add(sid, "WARN", "rule 28", "4–8 character line — about 1 in 6 come out garbled; 9+ is safer")
        dups = [n for n in DUP_NOUNS if prompt.count(n) >= 2]
        if dups:
            add(sid, "WARN", "rule 28", f"'{dups[0]}' is named twice — the model may draw two of them; refer back with 它/上面")
        if len(in_frame) <= 2 and re.search(r"環抱|張開手臂|手臂張開|張開的手", prompt) and not re.search(r"一個人|自己一個|只有他|只有她", prompt):
            add(sid, "WARN", "rule 28", "an open-armed pose implies someone to hold — say how many people are there (她一個人…)")
        # 規則 29：改變東西的動作要有開始跟結束（削著／切著／一直 → 來回重做）—— 正式管線 rulecheck 的 _no_end
        for seg in _no_end(prompt):
            add(sid, "ERROR", "rule 29", f"'{seg.strip()}' has no start and end — write each step and the result (削斷、切成兩半、吃掉)")
        # 規則 30：沒首幀的鏡第一句要寫起點（站在哪、面向哪）—— 正式管線只提醒（證據一鏡）；s061_07、13 沒起點跑到戶外／長出百葉窗
        #    只看兩人同框：誰面向鏡頭沒講清楚才會換人講話、跑景（單人鏡的對照組幾乎每鏡都會叫，誤報太多）
        if no_first_frame and len(in_frame) >= 2 and not re.search(START_POS, re.split(r"[，。；]|" + STEP_WORDS, prompt)[0]):
            add(sid, "WARN", "rule 30", "no first frame and the first clause doesn't say where they stand / which way they face — the framing is left to chance")
        # 規則 31：室內往後拉 —— 拉遠露出的區域會長出東西（正式管線症狀表 162、s061_16 第一版長出一張紙）
        if s.get("camera_move") == "DOLLY_OUT" and not s.get("_outdoor"):
            add(sid, "WARN", "rule 31", "DOLLY_OUT indoors keeps revealing walls the model must invent — push in (DOLLY_IN) instead, or start wide")

        # 規則 16：有台詞的鏡，表演不能叫他安靜 —— 跟口白打架，模型只能挑一個聽
        #（Nemotron 第一次真跑：為了躲「寫到嘴就會講」，八鏡寫了「最後保持安靜的姿勢」，其中五鏡有台詞）
        if line and re.search(r"安靜|沉默|不出聲|閉著嘴|閉嘴|默默", prompt):
            add(sid, "ERROR", "rule 16", f"the shot has a line but the performance says to stay silent ({re.search(r'安靜|沉默|不出聲|閉著嘴|閉嘴|默默', prompt).group()}) — the two fight; describe a body action instead")
        # 規則 17：講話時不要低頭打字 —— 模型會把台詞當成打出來的字、配成旁白、嘴不動（實拍過的失敗）
        if line and role == "character" and re.search(r"打字|鍵盤|按手機|滑手機|點手機|滑動手機|手機螢幕|看手機|輸入", prompt):
            add(sid, "ERROR", "rule 17", "typing while speaking: the model treats the line as typed text and voices it as narration with a closed mouth — stop typing and face something first")
        # 規則 18：道具只存在於它所在的地點（手機在月音房間，不會跑進深息的虛空）
        props = scene.get("props") or {}
        for prop, where in props.items():
            # 「手機」包在「白色手機」裡時只報長的那個，同一件東西不報兩次
            if any(prop != q and prop in q and q in prompt for q in props):
                continue
            if prop in prompt and locs and not locs & set(where):
                add(sid, "ERROR", "rule 18", f"{prop!r} only exists in {where}, but this shot is in {sorted(locs)}")
        # 規則 25：不要比手畫腳 —— 吵架的人不會比出書的形狀、指耳朵代表歌曲、雙手比愛心。
        #   兩種都量得出來：用手「比」出東西；用動作「代表／表示／象徵」某個意思（模型畫不出「代表」，只會照字面比劃）。
        #（給範例之後運鏡對了，卻冒出一整排比手畫腳：比畫書的形狀、指向耳朵代表歌曲、雙手比愛心。）
        mime = re.search(r"比畫|比劃|比划|比出|比成|比個|比愛心|雙手比|的形狀|代表|表示|象徵|示意", prompt)
        if mime:
            add(sid, "ERROR", "rule 25", f"miming ({mime.group()}): people in a scene don't act out words or explain a meaning — "
                                         "give a real body action (grip the sink, step closer, turn away)")

        # 規則 5 補充：「保持某某姿勢」不是動作，是填空 —— 它不佔時間，也不告訴模型要做什麼
        if re.search(r"(最後|然後)\s*，?\s*保持", prompt):
            add(sid, "WARN", "rule 5", "'keep a pose' is not an action — end on something the body actually does")

        # 規則 4、5：一步一步、開口前要有身體動作
        if len(re.findall(STEP_WORDS, prompt)) < 2:
            add(sid, "WARN", "rule 5", "fewer than 3 ordered actions (use 之後 / 然後 / 最後)")
        if line and role == "character" and _pre_speech_face_only(prompt):
            add(sid, "ERROR", "rule 4", "only face micro-actions before the line — add a visible body action or the gap fills with gibberish")

    # ── 手藝關卡：上面那些只擋「會壞」，這裡擋「會平」 ──────────────────────────
    # 為什麼要有：只擋風險的關卡，會把作品一章一章推向平淡（正式製作第四章 38 鏡有運鏡 → 第七章 0 鏡）。
    # Nemotron 導第 61 場：22 鏡全中景、全不動、Lisa 每鏡「輕輕點頭」—— 全部關卡都過了。
    # 量得出來的平淡就做成關卡；量不出來的（情緒弧線對不對）交給評審（critic.py）。
    if len(shots) >= 6:
        # 規則 22：景別要有變化、要有會動的鏡頭
        types = [s.get("shot_type") for s in shots]
        top, n = max(((t, types.count(t)) for t in set(types)), key=lambda x: x[1])
        if n / len(shots) > 0.75:
            add("scene", "ERROR", "rule 22", f"{n} of {len(shots)} shots are {top} — vary the framing with the emotion (wide to set the space, close when it hurts)")
        if all(s.get("camera_move") in ("BREATHE", "STATIC") for s in shots):
            add("scene", "ERROR", "rule 22", "the camera never moves in the whole scene — let at least one move earn its place (push in on the hardest line, pull back as she leaves)")
    # 規則 23：同一句表演抄到三鏡以上 = 公式，不是表演
    #   🔴 純「看著某人」不算：面對面吵架，看著對方是一直持續的狀態，不是一個反應。
    #   實測：這條把「凝視Lisa」也算進去，模型就一路換同義詞（凝視→注視→看著→觀察 Lisa 的臉）、動作根本沒變，
    #   八回合卡在這裡沒過 —— 規則原意是「反應要升級」，被我擴大成「任何句子都不能重複」。
    for clause, ids in _clauses(shots).items():
        if len(ids) >= 3:
            add("scene", "ERROR", "rule 23", f"'{clause}' is repeated in {len(ids)} shots ({', '.join(ids)}) — a reaction should change as the scene escalates")
    # 規則 24：拆台詞要拆在句子結束的地方 —— 拆在句子中間，兩鏡各唸半句，語氣斷掉
    #    剛好唸到劇本某一句的結尾＝完整的一句，不必有句號：日文「」對白常常沒有「。」
    #    （第三世第 2 話：第 2 場 36 句有 23 句原文就沒有句尾符號 → 跟規則 9「照抄原文」打架，十輪改不掉）
    script_ends = [_norm(d.get("line", "")) for d in scene.get("dialogue") or [] if d.get("line")]
    raw_lines = [d.get("line", "") for d in scene.get("dialogue") or [] if d.get("line")]
    said = lambda x: [d.get("text", "") for d in x.get("dialogue_lines") or []] or ([x.get("dialogue")] if x.get("dialogue") else [])  # noqa: E731
    for i, s in enumerate(shots):
        texts = said(s)
        for t in texts:
            if t and _norm(t) and any(e.endswith(_norm(t)) for e in script_ends):
                continue
            # 句號是自己補的不算：劇本原句在這個位置後面還接著別的字（「でなくても、特定の…」）就是拆在句子中間
            #    （s003_19：模型把「、」改成「。」騙過這條；規則 9 比對時不看標點，兩條都沒擋）
            core = re.sub(r"[。？！?!…—～~\s]+$", "", t.strip())
            cut_inside = bool(core) and any(re.search(re.escape(core) + r"\s*[^。？！?!…—」』～~\s]", raw)
                                            for raw in raw_lines)
            if t and (cut_inside or not re.search(r"[。？！?!…—」』～~]\s*$", t.strip())):
                # 講出後半句在哪一鏡：只講「拆在句尾」模型會換個地方拆（s003_19 三次都只是挪逗號），要直接說「跟哪一鏡合併」
                nxt = next((x for x in shots[i + 1:] if said(x)), None)
                # 劇本原句在這裡就是句尾（後面接「。」），只是標點被拿掉 → 補回標點，不是合併
                end = next((m.group(1) for raw in raw_lines
                            for m in [re.search(re.escape(core) + r"\s*([。？！?!…—」』～~])", raw)] if m), None) if core else None
                if end and not cut_inside:
                    hint = f"; in the script this is where a sentence ends — end the line with the script's 「{end}」"
                else:
                    hint = (f"; the rest of the sentence is in {nxt.get('shot_id')} ('{said(nxt)[0].strip()[:14]}…') — "
                            f"merge the two into one shot (keep every word, give it enough seconds) and remove the other"
                            if nxt else "")
                add(s.get("shot_id"), "ERROR", "rule 24", f"line split mid-sentence: '{t.strip()[-12:]}' — split only where a sentence ends{hint}")

    # 鏡號不能重複 —— 渲染、驗收、重生都靠鏡號對應（Ultra 評審抓到三個重複鏡號，關卡當時沒擋）
    ids = [s.get("shot_id") for s in shots]
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        add(dup, "ERROR", "ids", f"shot_id {dup!r} is used {ids.count(dup)} times — renumber the shots in order")

    # 規則 9：台詞從劇本來，照順序，不刪字
    texts = [_norm(s.get("dialogue")) for s in shots]
    used, pos, prev_shot = set(), 0, None
    for d in scene["dialogue"]:
        L = _norm(d["line"])
        # 先找單一鏡；單鏡對不上才試「拆到相鄰兩鏡」——
        # 反過來找的話，兩鏡串起來的覆蓋率永遠不輸單鏡，會把台詞算到前一鏡頭上（第一版就這樣錯過）
        best = (0.0, None)
        for span in [(i, i) for i in range(pos, len(shots))] + [(i, i + 1) for i in range(pos, len(shots) - 1)]:
            cov = _coverage(L, "".join(texts[span[0]:span[1] + 1]))
            if cov > best[0] + 1e-9:
                best = (cov, span)
            if best[0] >= 0.95 and span[0] == span[1]:
                break
        cov, span = best
        if cov < 0.8:
            # 講清楚它該放哪：雲端實測一場掉了一句，訊息只說「少一句」，模型五回合都沒補回去、回合用完。
            # 「排在哪一鏡後面」是量得出來的（上一句對到的那一鏡），就由系統講，不叫模型自己找。
            where = (f"; it comes right after {prev_shot} (which carries the previous script line) — insert a new shot there"
                     if prev_shot else "; it is the first line — put it in the first speaking shot")
            add("scene", "ERROR", "rule 9", f"script line missing or changed: {d['line']!r} (best match {cov:.0%}), "
                                            f"spoken by {d.get('speaker', '?')}{where}, with the line copied exactly")
            continue
        used.update(range(span[0], span[1] + 1))
        pos = span[1]                     # 下一句可以從同一鏡開始（同一人連講兩句）
        prev_shot = shots[span[1]].get("shot_id")
        t = "".join(texts[span[0]:span[1] + 1])
        if len(L) >= 6 and len(t) > len(L) * 1.3 + 2:
            add(shots[span[0]].get("shot_id"), "WARN", "rule 9", f"words added to a full-length line: {d['line']!r}")
    for i, s in enumerate(shots):
        if texts[i] and i not in used:
            add(s.get("shot_id"), "WARN", "rule 9", f"line not in the script: {s.get('dialogue')!r}")
    # 規則 9 補：同一句台詞不能在兩鏡各講一次 —— 上面只查「每句都有、順序對」，沒查「講了幾次」。
    #（Ultra 評審抓到：生日那句在兩鏡、退場那句在兩鏡，整場變成鬼打牆；關卡全過。）
    for d in scene["dialogue"]:
        L = _norm(d["line"])
        if len(L) < 6:
            continue          # 短句（「什麼？」）本來就可能是不同的人各講一次
        # 用「整句原封不動出現」判斷，不用模糊比對：第 7 句「妳看過我們的對話嗎？」跟第 9 句開頭
        # 「妳看過我跟深息的對話嗎？」長得很像，模糊比對會把合法的拆句誤判成重複（第一版就誤報了）
        hits = [shots[i].get("shot_id") for i in range(len(shots)) if texts[i] and L in texts[i]]
        if len(hits) > 1:
            add("scene", "ERROR", "rule 9", f"script line spoken in {len(hits)} shots ({', '.join(hits)}): {d['line']!r} — each line is said once")
    # 規則 26：同一個地方兩個人一來一往 → 同框為主。只寫成規則沒用（實測：規則寫了，25 鏡 0 鏡同框，
    #   範例 18 鏡也只有 3 鏡同框，模型照範例切正反打），所以做成關卡：對白鏡至少四成要兩個人都在畫面裡。
    _two_shot_gate(chars, shots, add)
    return issues


TWO_SHOT_SHARE = 0.4


def _two_shot_gate(chars: dict, shots: list, add) -> None:
    """規則 26 的關卡：兩個講話的人在同一個地點，對白鏡裡同框的比例不夠就整場擋下，列出可以合併的正反打。"""
    def speakers(s):
        dl = s.get("dialogue_lines") or []
        return {x.get("speaker") for x in dl} if dl else ({s.get("speaker")} if s.get("dialogue") else set())

    dlg = [s for s in shots if s.get("dialogue") and (s.get("voice_role") or "character") == "character"]
    by_loc: dict = {}
    for c in set().union(*(speakers(s) for s in dlg)) if dlg else set():
        if c in chars:
            by_loc.setdefault(chars[c].get("location"), set()).add(c)
    pair = next((v for v in by_loc.values() if len(v) >= 2), None)
    if not pair or len(dlg) < 4:
        return
    two = [s for s in dlg if len(pair & set(s.get("characters_in_frame") or [])) >= 2]
    need = max(2, round(len(dlg) * TWO_SHOT_SHARE))
    if len(two) >= need:
        return
    # 列出「這一鏡一個人講、下一鏡換另一個人講」的正反打，那正是該合併成一鏡同框的地方
    flips = [f"{a.get('shot_id')}+{b.get('shot_id')}" for a, b in zip(dlg, dlg[1:])
             if speakers(a) != speakers(b) and len(a.get("characters_in_frame") or []) == 1
             and len(b.get("characters_in_frame") or []) == 1]
    names = " and ".join(chars[c].get("name_zh") or c for c in sorted(pair))
    # WARN 不是 ERROR：同框的格式由 director/two_shots.py 在導完之後用程式合（模型寫同框會連帶踩規則 6、9、21，
    #   實測十回合來回後放棄）。這裡只提醒模型「這幾組是正反打」，讓它表演寫得能接。
    add("scene", "WARN", "rule 26",
        f"{names} trade lines in the same place, but only {len(two)} of {len(dlg)} dialogue shots show both of them "
        f"(need at least {need}). Turn back-and-forth cuts into two-shots: both in characters_in_frame, blocking set, "
        f"the lines as dialogue_lines in speaking order, each person's action named"
        + (f". Candidates: {', '.join(flips[:6])}" if flips else ""))


GAZE_ONLY = re.compile(r"^(她|他|月音|Lisa|深息)?(直直|靜靜|冷冷|定定)?(地)?(看著|看向|望著|望向|凝視|注視|盯著|直視|觀察)"
                       r"(著)?(她|他|月音|Lisa|深息|對方|前方)?(的)?(臉|眼睛|雙眼)?$")


def _clauses(shots: list) -> dict:
    """規則 23 用的：把每鏡表演拆成短句，記下每一句出現在哪幾鏡（純「看著某人」不算，見規則 23 註解）。"""
    seen = {}
    for s in shots:
        for clause in {re.sub(r"\s+", "", x) for x in re.split(r"[，。；、]|" + STEP_WORDS, s.get("visual_prompt") or "")}:
            if len(clause) >= 4 and not GAZE_ONLY.match(clause):
                seen.setdefault(clause, []).append(s.get("shot_id"))
    return seen


def near_repeats(shots: list) -> str:
    """已經用在兩鏡的表演句 —— 再用一次就踩規則 23。改稿時一起交給模型。

    為什麼要有：雲端實測（含示範錄影那一場），模型修掉一句重複，換上的新寫法剛好撞到另一句已經用過兩次的，
    規則 23 一路 7→10→9→21 打地鼠，十回合沒過。哪些句子「已經用兩次」是量得出來的，就由系統量好交給它，不叫它自己數。
    """
    twice = [f"'{c}' ({', '.join(ids)})" for c, ids in _clauses(shots).items() if len(ids) == 2]
    if not twice:
        return ""
    return ("Already used in 2 shots — writing any of these into a third shot will fail rule 23, so pick a different, "
            "escalating action instead:\n" + "\n".join(twice))


def summarize(issues: list) -> str:
    """把問題清單印成導演看得懂的回饋（也直接拿去餵模型改稿）。"""
    if not issues:
        return "All gates passed."
    return "\n".join(f"[{i['level']}] {i['shot']} ({i['rule']}): {i['msg']}" for i in issues)


if __name__ == "__main__":
    scene = json.load(open(sys.argv[1], encoding="utf-8"))
    shots = json.load(open(sys.argv[2], encoding="utf-8"))
    shots = shots.get("shots", shots) if isinstance(shots, dict) else shots
    found = check(scene, shots, allow_waivers=True)   # 命令列是拿來跑對照組（人寫的）
    print(summarize(found))
    errors = sum(i["level"] == "ERROR" for i in found)
    print(f"\n{len(shots)} shots — {errors} errors, {len(found) - errors} warnings")
    sys.exit(1 if errors else 0)
