"""台詞關卡 —— 台詞導演每一版回覆都先過這裡，擋下的講給它聽、叫它改。

為什麼要程式擋、不靠提示詞叮嚀：作者做《第三世》日文動畫時，每一條都是踩過才知道的，
而且「寫在規則裡」擋不住（模型照樣把小說的書面語搬進台詞）。能用程式判斷的就變成關卡：

  ruby      讀音標記：日文每個漢字都要有讀音、讀音只能是假名；去掉標記後就是要唸的台詞
  target    真的改成原聲語言了沒（日文片的台詞裡沒有假名＝還是原文）
  kougo     私下講話要口語（〜ている→〜てる、〜ではない→〜じゃない…）；正式場合（身份、儀式、演給上級看）不擋。
            規則照作者的 作者管線的 kougo.py（第三世第05話起用的那一份）
  length    這一鏡的秒數唸不唸得完（唸不完 H3 會加速或吃字）
  subs      每一種字幕語言都要有、而且不能只是把原聲抄一遍
  tongue    中文原聲：同音字連發、j／q／x 短音連著，H3 會唸糊（作者 作者管線的 tongue.py，只提醒不擋）

每一筆問題：{"id": 台詞編號, "level": "ERROR"|"WARN", "rule": 關卡名, "msg": 給模型看的英文, "zh": 給人看的中文}。
"""

from __future__ import annotations

import re

# 原聲語言 → 每秒大約唸幾個「單位」（日／中文＝字、韓文＝字、英文＝字母）。
# 校準：第三世第01～04話 H3 日文實測一鏡 6 秒約 30～36 字；給一點餘裕，超過才擋。
RATE = {"ja": 6.5, "zh": 5.0, "ko": 5.5, "en": 15.0}
MIN_UNITS = 6   # 台詞最少字數（H3 實測，見 check 裡的 short 關卡）
# 日文不用的中文字（繁體常用字＋中文語助詞）。只收「日文裡不會出現」的，誤報比漏抓更煩
#   中文語助詞＋繁體字形跟日文不同的（會→会、對→対、從→従、與→与、讓→譲、樣→様、沒→没、裡→裏、說→説）
ZH_ONLY = "妳們這說麼嗎吧呢啊嘛哪裡沒對會從與讓樣"
LANG_NAME = {"ja": "Japanese", "zh": "Traditional Chinese (Taiwan)", "en": "English", "ko": "Korean"}

_KANJI = r"[一-鿿々〆ヶ]"
_RUBY = re.compile(r"(" + _KANJI + r"+|[ァ-ヴー]+)\[([^\]]*)\]")
_KANA_ONLY = re.compile(r"[ぁ-ゖァ-ヺー・ゝゞヽヾ]+")
_HAS = {
    "ja": re.compile(r"[ぁ-ゖァ-ヺ]"),
    "zh": re.compile(r"[一-鿿]"),
    "ko": re.compile(r"[가-힣]"),
    "en": re.compile(r"[A-Za-z]"),
}

# 書面語 → 口語（照 作者管線的 kougo.py，第三世第05話起的標準）
KOUGO = [
    (r"(?<!て)ている(?!から|ので)", "〜ている → 〜てる"),
    (r"ていた(?!だ)", "〜ていた → 〜てた"),
    (r"ていて", "〜ていて → 〜てて"),
    (r"ではない", "〜ではない → 〜じゃない"),
    (r"では(?=[、。？！]|$)", "では → じゃ"),
    (r"てしまう|てしまった", "〜てしまう → 〜ちゃう／ちゃった"),
    (r"なければ(?:ならない|いけない)?", "〜なければ → 〜ないと／〜なきゃ"),
    (r"のだ(?=[。、？！]|$)", "〜のだ → 〜んだ"),
    (r"のです", "〜のです → 〜んです"),
    (r"である", "〜である → 〜だ"),
    (r"ねばならない", "〜ねばならない → 〜なきゃ"),
    (r"(?<![ご])存じ", "存じ → 知って"),
]
# 不擋、只提醒（越熟越該用）
KOUGO_SOFT = [
    (r"ください", "〜ください → 〜て／〜ちょうだい（熟了以後）"),
    (r"という(?:のは|こと)", "〜というのは → 〜って"),
    (r"それでは", "それでは → じゃあ"),
    (r"とても", "とても → すごく"),
    (r"けれど(?!も)", "けれど → けど"),
    (r"なぜ", "なぜ → なんで"),
]
CASUAL = {"casual", "intimate"}
REGISTERS = ["intimate", "casual", "polite", "formal", "honorific", "narration"]


def strip_ruby(ruby: str) -> str:
    """讀音標記 → 要唸的台詞（漢字[よみ] → 漢字）。"""
    return _RUBY.sub(lambda m: m.group(1), ruby or "")


def tokens(ruby: str) -> list[tuple[str, str | None]]:
    """讀音標記 → [(字, 讀音或 None)]，畫字幕用（讀音標在那一串字的正上方）。"""
    out: list[tuple[str, str | None]] = []
    pos = 0
    for m in _RUBY.finditer(ruby or ""):
        out += [(ch, None) for ch in ruby[pos:m.start()]]
        out.append((m.group(1), m.group(2)))
        pos = m.end()
    out += [(ch, None) for ch in (ruby or "")[pos:]]
    return out


def zh_ruby(text: str) -> str:
    """沒有讀音標記的中文台詞（使用者自己打的）→ 程式補拼音。
    只當備案：pypinyin 對繁體詞組會查錯（銀行→yín xíng），主線讀音由台詞導演寫、zh_reading_ok 檢查。"""
    from pypinyin import Style, pinyin

    # 一個字一個字查會把多音字查錯（行→xíng／háng）：漢字串起來整句查（pypinyin 會照詞組判），再一個一個對回去
    han = [ch for ch in text if "一" <= ch <= "鿿"]
    py = iter(p[0] for p in pinyin("".join(han), style=Style.TONE))
    return "".join(f"{ch}[{next(py)}]" if "一" <= ch <= "鿿" else ch for ch in text)


_tagger = None


def _dict_readings(spoken: str) -> dict[int, tuple[str, str]]:
    """字典（fugashi＋unidic-lite）查的讀音：{在台詞裡的位置: (漢字串, 讀音, 有沒有送假名)}。只收「漢字串＋送假名」能拆乾淨的詞。

    用途兩個：模型漏標的漢字由程式補（3 版都沒補齊的實測：話、問題、最近、言），
    以及模型標的讀音跟字典不一樣時提醒（實測：顔色 標成 かお＋しょく，字典是 かおいろ）。
    字典不懂上下文也不懂人名，所以只補、只提醒、跨詞關卡也只在讀音對不上時才擋。
    為什麼不用 pykakasi：它是 GPL-3.0，EXE 會把它一起散布 → 整個 EXE 得照 GPL；
    fugashi（MIT）＋unidic-lite（UniDic 可選 BSD）授權乾淨。第三世 286 句誤報掃描兩者差不多。
    """
    global _tagger
    try:
        import fugashi
    except ImportError:
        return {}
    if _tagger is None:
        _tagger = fugashi.Tagger()
    out, pos = {}, 0
    for w in _tagger(spoken):
        orig = w.surface
        hira = "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in (w.feature.kana or ""))
        m = re.fullmatch(r"(" + _KANJI + r"+)([ぁ-ゖ]*)", orig)
        if m and hira and hira.endswith(m.group(2)):
            reading = hira[: len(hira) - len(m.group(2))] if m.group(2) else hira
            if reading:
                out[pos] = (m.group(1), reading, bool(m.group(2)))   # 第三格：有沒有送假名
        pos += len(orig)
    return out


def ja_punct(ruby: str) -> str:
    """日文台詞裡的中文全形逗號換成読点（實測：中文劇本改寫時模型常把「，」帶過來）。"""
    return (ruby or "").replace("，", "、")


def fill_ruby(ruby: str) -> tuple[str, list[str]]:
    """日文：模型漏標讀音的漢字，用字典補上。回傳（補好的標記, 補了哪些）。"""
    spoken = strip_ruby(ruby)
    found = _dict_readings(spoken)
    out, filled, pos = [], [], 0
    toks = tokens(ruby)
    i = 0
    while i < len(toks):
        base, rb = toks[i]
        if rb is None and re.fullmatch(_KANJI, base) and pos in found:
            run, reading, _ = found[pos]
            if strip_ruby("".join(b for b, _ in toks[i:i + len(run)])) == run:
                out.append(f"{run}[{reading}]")
                filled.append(f"{run}[{reading}]")
                pos += len(run)
                i += len(run)
                continue
        out.append(f"{base}[{rb}]" if rb else base)
        pos += len(base)
        i += 1
    return "".join(out), filled


def reading_doubts(ruby: str) -> list[str]:
    """日文：模型標的讀音跟字典不一樣的地方（只提醒）。"""
    found = _dict_readings(strip_ruby(ruby))
    doubts, pos = [], 0
    toks = tokens(ruby)
    for k, (base, rb) in enumerate(toks):
        if rb:
            # 模型可能把一個詞拆成幾段標（顔[かお]色[しょく]）→ 合起來跟字典那個詞比
            for start, (run, reading, okuri) in found.items():
                if start <= pos < start + len(run):
                    if start == pos and run == base and rb != reading and (len(run) > 1 or okuri):   # 單一個漢字獨立出現（君、何）字典常唸錯，不提醒
                        doubts.append(f"{base}[{rb}]（字典：{reading}）")
                    elif start == pos and len(run) > len(base):
                        joined, q, n = "", k, 0
                        while q < len(toks) and n < len(run) and toks[q][1]:
                            joined += toks[q][1]
                            n += len(toks[q][0])
                            q += 1
                        if n == len(run) and joined != reading:
                            doubts.append(f"{run}[{joined}]（字典：{reading}）")
                    break
        pos += len(base)
    return doubts


def zh_reading_ok(ch: str, reading: str) -> bool:
    """這個讀音是不是這個字真的有的唸法（多音字列全部）。查不到的字不擋。"""
    from pypinyin import Style, pinyin

    options = set(pinyin(ch, style=Style.TONE, heteronym=True)[0])
    return not options or ch in options or reading.strip().lower() in {o.lower() for o in options}


def tongue(line: str) -> list[str]:
    """中文台詞會唸糊的地方（照 作者管線的 tongue.py；幕後短片 202 句校準，正片只當提醒）。"""
    from pypinyin import Style, lazy_pinyin

    skip = set("的了嗎呢吧啊喔嘛耶囉")
    chars = [ch for ch in line if "一" <= ch <= "鿿"]
    py = lazy_pinyin("".join(chars), style=Style.TONE3, neutral_tone_with_five=True)
    short = lambda x: re.fullmatch(r"[jqx][iuv]\d", x) is not None  # noqa: E731
    bad = []
    for pa in dict.fromkeys(py):
        idx = [k for k, x in enumerate(py) if x == pa and chars[k] not in skip]
        if len(idx) >= 3 and len({chars[k] for k in idx}) >= 2:
            bad.append(f"同音「{'／'.join(dict.fromkeys(chars[k] for k in idx))}」({pa}×{len(idx)})")
    for i in range(len(chars) - 1):
        if short(py[i]) and short(py[i + 1]) and chars[i] != chars[i + 1]:
            bad.append(f"連音「{chars[i]}{chars[i + 1]}」")
    return bad


def speak_units(text: str, lang: str) -> int:
    """唸起來的長度單位（標點、空白不算）。"""
    if lang == "en":
        return len(re.sub(r"[^A-Za-z]", "", text))
    return len(re.sub(r"[\s、。，,.！!？?…「」『』（）()—\-~〜・：:;；\"']", "", text))


def max_units(seconds: float, lang: str) -> int:
    """一鏡最多唸幾個單位（留 0.8 秒給開口前與講完後的表演）。"""
    return max(4, int((max(seconds, 1.0) - 0.8) * RATE.get(lang, 5.0)))


def in_language(text: str, lang: str) -> bool:
    """這段解說是不是用這個語言寫的（第61場實測：要中文的「為什麼這樣說」整批寫成日文）。"""
    t = re.sub(r"「[^」]*」|『[^』]*』|\"[^\"]*\"|“[^”]*”", "", text or "")   # 引用的原聲台詞不算
    kana = len(re.findall(r"[ぁ-ゖァ-ヺ]", t))
    han = len(re.findall(r"[一-鿿]", t))
    latin = len(re.findall(r"[A-Za-z]", t))
    hangul = len(re.findall(r"[가-힣]", t))
    total = max(1, kana + han + latin + hangul)
    if lang == "zh":
        return kana / total < 0.15 and han / total > 0.4
    if lang == "ja":
        return kana / total > 0.2
    if lang == "en":
        return latin / total > 0.6
    if lang == "ko":
        return hangul / total > 0.4
    return True


def check_notes(notes: list[dict], explain: str) -> list[dict]:
    """學習筆記的關卡：要用解說語言寫。"""
    out = []
    for n in notes:
        if not in_language((n.get("point") or "") + (n.get("explain") or ""), explain):
            out.append({"id": n.get("line_id"), "level": "ERROR", "rule": "explain",
                        "msg": f"the study note on {n.get('line_id')} must be written in {LANG_NAME.get(explain, explain)}",
                        "zh": "學習筆記不是用解說語言寫的"})
    return out


def check(lines: list[dict], replies: list[dict], film: str, learners: list[str],
          people: dict[str, str] | None = None, en_names: dict[str, str] | None = None,
          name_say: dict[str, str] | None = None) -> list[dict]:
    """檢查一版回覆。lines＝送給模型的台詞（id、seconds、shot_id、voice_role、source），replies＝模型回的每一句。
    people＝{角色名: 是什麼人（"human woman, office worker"）}，用來擋人稱和名字。"""
    people = people or {}
    issues: list[dict] = []
    want = {ln["id"]: ln for ln in lines}
    got: dict[str, dict] = {}

    def add(lid, level, rule, msg, zh):
        issues.append({"id": lid, "level": level, "rule": rule, "msg": msg, "zh": zh})

    for r in replies:
        lid = r.get("id")
        if lid not in want:
            add(lid, "ERROR", "ids", f"{lid} is not one of the line ids you were given", f"{lid} 不是給它的台詞編號")
            continue
        if lid in got:
            add(lid, "ERROR", "ids", f"{lid} appears twice", f"{lid} 回了兩次")
        got[lid] = r
    for lid in want:
        if lid not in got:
            add(lid, "ERROR", "ids", f"{lid} is missing from your reply", f"{lid} 漏掉了")

    # 同一鏡的台詞加起來要唸得完
    per_shot: dict[str, int] = {}
    for lid, r in got.items():
        ruby = (r.get("ruby") or "").strip()
        spoken = strip_ruby(ruby)
        if not spoken:
            add(lid, "ERROR", "target", "the line is empty", "台詞是空的")
            continue
        if "[" in spoken or "]" in spoken:   # 讀音標在假名上（違う[ちがう]、の[の]）→ 去不掉、會被唸出來
            add(lid, "ERROR", "ruby", "readings go on kanji only — remove brackets after kana (違[ちが]う, not 違う[ちがう])",
                "讀音標在假名上（去不掉，會被當成台詞）")
        if learners and r.get("why") and not in_language(r["why"], learners[0]):
            add(lid, "ERROR", "explain", f"'why' must be written in {LANG_NAME.get(learners[0], learners[0])} for the learner",
                "「為什麼這樣說」不是用學習者的語言寫的")
        if r.get("register") not in REGISTERS:
            add(lid, "ERROR", "register", f"register must be one of {', '.join(REGISTERS)}", f"語氣標籤「{r.get('register')}」不合法")
        src = (want[lid].get("source") or "").strip()
        # 對位：每一句只能改寫自己那一句。第61場實測：模型把兩句併成一句，後面每一句都往前錯一位、講者全對不上
        if "source" in r and src and (r.get("source") or "").strip() != src:
            add(lid, "ERROR", "align",
                f"{lid} must rewrite ITS OWN script line \"{src}\" (you answered for \"{(r.get('source') or '')[:40]}\"). "
                "Do not merge, split or shift lines: one id, one line.",
                "對錯句了（併句或錯位）")
        # 名字是名字：第61場實測把 AI 角色「深息」翻成「深呼吸」
        #    寫成名字表的唸法也算留住（Ultra 直接寫シェンシー、つきね —— 第二世日文稿也是這樣寫）
        for name in people:
            said = (name_say or {}).get(name)
            kept = name in spoken or (said and said in spoken)
            if re.search(r"[一-鿿]", name) and name in src and not kept and film in ("ja", "zh"):
                add(lid, "ERROR", "names", f"keep the name {name} as a name (write it as {name}"
                    + ("[reading]" if film == "ja" else "") + (f" or {said}" if said else "")
                    + "), never translate its meaning", f"名字「{name}」被改掉了")
        # 外文單字混進台詞（第61場實測：日文台詞裡冒出西班牙文 respeto）。AI、名字、劇本原句本來就有的外文不算
        if film in ("ja", "zh", "ko"):
            allowed = {w.lower() for w in re.findall(r"[A-Za-z]+", src + " " + " ".join(people))} | {"ai"}
            stray = [w for w in re.findall(r"[A-Za-z]{2,}", spoken) if w.lower() not in allowed]
            if stray:
                add(lid, "ERROR", "foreign", f"foreign words in a {LANG_NAME[film]} line: {', '.join(stray)} — say it in {LANG_NAME[film]}",
                    f"台詞裡混了外文：{'、'.join(stray)}")
        # 英文字幕的名字照名字表（深息＝Shenxi，第二世英文版正典；模型自己會拼成 ShinSoku）
        en_sub = (r.get("subs") or {}).get("en") or ""
        for name, en in (en_names or {}).items():
            if "en" in learners and name in src and en_sub and en.lower() not in en_sub.lower():
                add(lid, "ERROR", "names", f"in the English subtitle, write the name {name} as {en}", f"英文字幕的「{name}」要寫成 {en}")
        speaker = want[lid].get("speaker") or ""
        if film == "ja" and re.search(r"\b(woman|girl|female)\b", people.get(speaker, "")) and re.search(r"俺|お前|てめえ|貴様", spoken):
            add(lid, "ERROR", "persona", f"{speaker} is a woman ({people[speaker]}): 俺／お前 is rough male speech — "
                "use わたし／あたし and あなた／名前 unless she truly talks like that", "女性角色用了男性粗口（俺／お前）")
        # 全漢字的短句（「何？」）沒有假名但有假名讀音，也算日文
        is_ja_by_ruby = film == "ja" and any(rb and _KANA_ONLY.fullmatch(rb) for _, rb in tokens(ruby))
        if film in _HAS and not _HAS[film].search(spoken) and not is_ja_by_ruby:
            add(lid, "ERROR", "target", f"this is not {LANG_NAME[film]} yet: rewrite it in {LANG_NAME[film]}",
                f"還不是{ {'ja': '日文', 'zh': '中文', 'en': '英文', 'ko': '韓文'}[film] }（像是沒改，還是原文）")
        if film == "ja":
            for m in re.finditer(r"\[([^\]]*)\]", ruby):
                if not _KANA_ONLY.fullmatch(m.group(1) or ""):
                    add(lid, "ERROR", "ruby", f"reading [{m.group(1)}] must be kana only", f"讀音「{m.group(1)}」不是假名")
            bare = _RUBY.sub("", ruby)
            missing = sorted(set(re.findall(_KANJI, bare)))
            if missing:
                add(lid, "ERROR", "ruby", f"kanji without a reading: {''.join(missing)} — write every kanji run as 漢字[よみ]",
                    f"漢字「{''.join(missing)}」沒標讀音")
            # 一個讀音蓋了兩個詞（前回私[わたし]）：去掉標記後「前回」等於沒讀音，上面那關抓不到。
            #    🔴 誤報掃描：只看「有沒有跨過詞的邊界」在第三世已定稿 286 句誤報 66 處（千年前[せんねんまえ]、
            #    四十七、名字）→ 改看讀音：模型的讀音連「第一個詞」的字典讀音都對不上才擋；字典不認得的（名字）放行
            found = _dict_readings(spoken)
            pos = 0
            for base, rb in tokens(ruby):
                inner = [s for s in found if pos < s < pos + len(base)]
                first = found.get(pos)
                numeric = base[0] in "一二三四五六七八九十百千万零〇"   # 數字有好幾種正確唸法（しじゅう／よんじゅう），字典只認一種
                if (rb and len(base) > 1 and base not in people and inner and first and not numeric
                        and len(first[0]) < len(base) and not rb.startswith(first[1])):
                    add(lid, "ERROR", "ruby", f"{base}[{rb}] covers more than one word — give each word its own reading "
                        f"({first[0]} is read {first[1]})", f"「{base}」是兩個以上的詞，讀音要分開標（{first[0]}＝{first[1]}）")
                pos += len(base)
            doubts = reading_doubts(ruby)
            if doubts:
                add(lid, "WARN", "reading", "check these readings against a dictionary: " + ", ".join(doubts),
                    "讀音可能錯：" + "、".join(doubts))
            if (r.get("register") or "") in CASUAL:
                hits = [tip for pat, tip in KOUGO if re.search(pat, spoken)]
                if hits:
                    add(lid, "ERROR", "kougo",
                        f"register is {r.get('register')} but the line is written like a novel: {'; '.join(hits)}. "
                        "Use the spoken form, or mark the register formal/polite if this speaker really talks that way here.",
                        "私下講話卻是書面語：" + "、".join(hits))
        if film == "zh":
            # 讀音交給模型（它看得懂上下文：銀行的行唸 háng）；程式只確認每個字都有、而且是這個字真的有的唸法
            for m in _RUBY.finditer(ruby):
                if len(m.group(1)) != 1:
                    add(lid, "ERROR", "ruby", f"{m.group(1)}[{m.group(2)}]: one character, one reading — write 字[zì] for each",
                        f"「{m.group(1)}」要一個字一個讀音")
                elif not zh_reading_ok(m.group(1), m.group(2)):
                    add(lid, "ERROR", "ruby", f"{m.group(1)} cannot be read as {m.group(2)}", f"「{m.group(1)}」沒有「{m.group(2)}」這個唸法")
            missing = sorted(set(re.findall(r"[一-鿿]", _RUBY.sub("", ruby))))
            if missing:
                add(lid, "ERROR", "ruby", f"characters without pinyin: {''.join(missing)} — write each as 字[pīnyīn]",
                    f"「{''.join(missing)}」沒標拼音")
            bad = tongue(spoken)
            if bad:
                add(lid, "WARN", "tongue", "hard to pronounce clearly: " + ", ".join(bad), "可能唸糊：" + "、".join(bad))
        # 日文台詞混了日文沒有的中文字（Ultra 實測：「妳、前主動で『最近どう？』って聞いたの」—— 有假名所以上面那關沒擋）
        if film == "ja":
            zh_only = sorted(set(re.findall(f"[{ZH_ONLY}]", spoken)))
            if zh_only:
                add(lid, "ERROR", "target", f"Chinese-only characters in a Japanese line: {''.join(zh_only)} — write it in Japanese",
                    f"日文台詞混了中文字：{''.join(zh_only)}")
        # 太短 H3 唸成亂音：作者本機管線第四章實測「≤3 字整句唸成亂音（三鏡全中，零誤報）」→ 定「台詞至少六個字」
        #    （ComfyUI/開工.sh 印的導演筆記第二節）。第61場實測台詞導演會寫出「何？」「えっ？」
        #    誤報掃描：一部已定稿動畫的 286 句日文有 25 句 4～5 字（見ていた。相手は？），人工都收了
        #    → 只有實證的「中文 ≤3 字」當硬錯；日文 ≤3 字第三世也收過（相手は？、百三回。）沒有實證 → 只提醒
        units = speak_units(spoken, film)
        if film in ("ja", "zh", "ko") and units < MIN_UNITS:
            add(lid, "ERROR" if units <= 3 and film == "zh" else "WARN", "short",
                f"too short for the video model ({units} characters; 3 or fewer comes out garbled) — "
                "say a natural, slightly longer version that keeps the meaning (何？ → え、何の話してるの？)",
                f"太短（{units} 字；3 字以下影片模型會唸成亂音）")
        sid = want[lid].get("shot_id") or lid
        per_shot[sid] = per_shot.get(sid, 0) + speak_units(spoken, film)

        subs = r.get("subs") or {}
        for lg in learners:
            s = (subs.get(lg) or "").strip()
            if not s:
                add(lid, "ERROR", "subs", f"subtitle in {LANG_NAME.get(lg, lg)} is missing", f"缺 {lg} 字幕")
            elif lg != film and s == spoken:
                add(lid, "ERROR", "subs", f"the {lg} subtitle just copies the spoken line", f"{lg} 字幕只是把原聲抄一遍")

    for sid, units in per_shot.items():
        sec = next((ln["seconds"] for ln in lines if (ln.get("shot_id") or ln["id"]) == sid), 0)
        cap = max_units(sec, film)
        if sec and units > cap:
            ids = [ln["id"] for ln in lines if (ln.get("shot_id") or ln["id"]) == sid]
            add(ids[0], "ERROR", "length",
                f"{'/'.join(ids)} must be spoken within {sec:.0f} s (about {cap} units) but has {units} — shorten it, keep the meaning",
                f"這一鏡 {sec:.0f} 秒唸不完（{units} 字，最多約 {cap}）")
    return issues


def hints(spoken: str, film: str) -> list[str]:
    """不擋的口語化建議（畫面上給使用者看）。"""
    if film != "ja":
        return []
    return [tip for pat, tip in KOUGO_SOFT if re.search(pat, spoken)]
