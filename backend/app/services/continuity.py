# -*- coding: utf-8 -*-
"""接首尾幀的執行期防呆。

分鏡 agent（gemini-3.1-pro-preview）會在 `Shot.first_frame_from` 標「這一鏡
接前一鏡的末幀」。實測它多半判得好 —— 每一場的第一鏡標硬切，
場內也會主動斷點。

但這是**機率性輸出**，判得好是這一次的結果、不是保證（同一份規則
另一次可能跑出全部不接）。所以硬條件在 code 層擋，
不靠 prompt 講清楚 —— 能用架構解的不要用提示詞解。

為什麼放在**執行期**而不是分鏡的後處理：
   分鏡表已經存進 Firestore 的專案不必重新分鏡就受益。
   重新分鏡會作廢整段進度，為了套一個防呆讓人重跑一遍不合理。
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class ContinuityDecision:
    """一個鏡頭「到底接不接前一鏡」的最終裁決。

    Attributes:
        allowed: True = 真的去抽上一鏡的末幀來接。
        source_shot_id: 接誰。allowed=False 時為 None。
        position: 這一鏡在接幀鏈裡的位置（鏈頭 = 1）。
        note: 人話說明，會進 clip warnings 給使用者看。
            否決時一定要有 note —— 分鏡表上寫著「接 s031_09」而實際硬切，
            使用者看到的成品跟他看到的表對不上，那是最難查的那種落差。
    """

    allowed: bool
    source_shot_id: str | None
    position: int
    note: str = ""
    same_scene_break: bool = False
    """斷鏈了，但**還在同一個場次**。

    跟跨場次的斷點意義相反：跨場次是應該換場景，這個是不該換
       但被迫斷開（鏈長上限、或畫面內角色跟來源鏡對不上）。
       參考圖只鎖角色不鎖場景，所以這種斷點會讓模型重新想像場景
       （實際觀察到成品換了場景）。
       呼叫端看到這個旗標要在提示詞裡補一句場景延續。
    """
    scene_anchor_source: str | None = None
    """同場次被迫斷鏈時，建議拿哪一鏡的末幀當**場景參考圖**。

    斷鏈鏡不能接幀（臉要靠角色卡），但場景延續光靠提示詞一句話
       是文字錨、比圖片錨弱。把來源鏡的末幀以「場景參考」身份
       混進 reference_images（跟角色卡同一組、位置在最後），
       臉靠卡、場景靠末幀，兩頭都有圖可依。
    只在 same_scene_break 時有值 —— 跨場次本來就該換場景，不給錨。
    """


def _identity_break(shot, src) -> str | None:
    """接幀對「臉」安不安全的裁決。回 None＝安全，回字串＝斷鏈理由。

    接幀繼承的是**上一鏡末幀那一格畫面**，臉的依據只有那一格裡看得到的人。
    兩種結構上就不安全的情況：
      1. **這一鏡有來源鏡沒有的角色** —— 那格裡沒有他的臉，
         模型只能靠文字標籤憑空生一張（實際觀察：中途入鏡的角色
         變成另一個人）。
      2. **這一鏡有兩人以上** —— 即使名單跟來源鏡相同，末幀那一格
         也不保證每張臉都在畫面裡（可能剛好出鏡、背對、被切掉），
         我們又不做人臉辨識去驗那一格，所以保守斷開、人手一張卡。

    單人鏡且來源鏡也有他 → 接幀最穩（首幀就是他本人），維持原路。
    """
    chars = set(getattr(shot, "characters_in_frame", None) or [])
    src_chars = set(getattr(src, "characters_in_frame", None) or [])
    if len(chars) >= 2:
        return (
            f"這一鏡有 {len(chars)} 個角色同框 —— 接幀只鎖得住末幀那一格"
            "看得到的臉，改用角色參考圖人手一張卡。"
        )
    new = chars - src_chars
    if new:
        return (
            f"角色 {'、'.join(sorted(new))} 在來源鏡 {src.shot_id} 的畫面裡"
            "不存在，末幀裡沒有他的臉可以繼承 —— 改用角色參考圖。"
        )
    return None


def _chain_positions(shots: list, max_chain: int, cards_with_frame: bool = False) -> dict[str, int]:
    """算出每一鏡在接幀鏈裡的**有效**位置。

    為什麼要有這個函數？
    鏈長上限不能用「原始鏈」直接算 —— 一條 8 鏡的鏈在第 5 鏡被斷開之後，
    第 5 鏡就變成新鏈頭，第 6 鏡的位置應該是 2 而不是 6。
    不重算的話後面整條都會被連坐否決，一條長鏡頭會全部退化成硬切。

    設計來源：「不是每個鏡頭都要接」的兩道防呆之一。

    依賴 shots 的順序就是依賴順序（分鏡表本來就是照時間排的）。
       如果哪天 first_frame_from 可以往後指，這裡要改成拓撲排序。

    Args:
        shots: 整段的鏡頭列表（順序即播放順序）
        max_chain: 鏈長上限，超過就斷開重新錨定

    Returns:
        dict: shot_id → 位置（1 = 鏈頭 / 硬切）
    """
    by_id = {s.shot_id: s for s in shots}
    pos: dict[str, int] = {}
    for shot in shots:
        src_id = getattr(shot, "first_frame_from", None)
        src = by_id.get(src_id) if src_id else None
        # 沒標接、或來源不在這段裡 → 自己就是鏈頭
        if src is None:
            pos[shot.shot_id] = 1
            continue
        # 跨場次 → 一律斷（換場景本來就該硬切）
        if getattr(src, "scene_number", None) != getattr(shot, "scene_number", None):
            pos[shot.shot_id] = 1
            continue
        # 臉的依據不足 → 斷（跟 decide() 同一個判準，兩邊必須一致，
        # 否則位置計算跟實際執行對不上、鏈長上限會算錯）
        if not cards_with_frame and _identity_break(shot, src):
            pos[shot.shot_id] = 1
            continue
        nxt = pos.get(src_id, 1) + 1
        # 鏈太長 → 這一鏡當新鏈頭，後面接著它的鏡頭從 2 重新數
        pos[shot.shot_id] = 1 if nxt > max_chain else nxt
    return pos


def decide(shot, shots: list, *, max_chain: int = 4, cards_with_frame: bool = False) -> ContinuityDecision:
    """裁決這一鏡到底接不接上一鏡的末幀。

    為什麼要有這個函數？
    `first_frame_from` 是 agent 的**意圖**，不是可以直接執行的指令。
    兩種情況執行了會比不執行更糟：
      1. **跨場次接幀** —— 換了場景還沿用上一場的最後一格，
         等於把前一場的背景帶進新場景，那是明確的錯誤畫面。
      2. **鏈拉太長** —— 一路接下去會累積漂移，第 8 鏡可能已經
         不太像第 1 鏡了。斷開回到角色參考圖重新錨定，
         代價是那一鏡有輕微跳接感，比漂到不像同一個人好。

    設計來源：判斷仍然歸 agent（它判得對），code 只擋結構上不可能對的那兩種。

    這裡**只做結構判斷，不做創作判斷**。「這兩鏡該不該連戲」是
       分鏡師的工作，留給 agent；code 只回答「連了會不會壞」。
    不能刪：刪了跨場次接幀會直接生出錯場景的畫面。

    Args:
        shot: 要生成的那一鏡（需要 shot_id / scene_number / first_frame_from）
        shots: 整段的鏡頭列表，用來追鏈與查來源鏡
        max_chain: 鏈長上限，來自 pricing.yaml 的 `max_continuity_chain`
            （不寫死：這個數字還沒有實測依據，之後要用 ClickHouse 的
            實際重拍率校準）

    Returns:
        ContinuityDecision: allowed=False 時 note 一定有值
    """
    src_id = getattr(shot, "first_frame_from", None)
    if not src_id:
        return ContinuityDecision(False, None, 1)

    by_id = {s.shot_id: s for s in shots}
    src = by_id.get(src_id)
    if src is None:
        return ContinuityDecision(
            False, None, 1,
            f"分鏡表要接 {src_id}，但這一段裡沒有這一鏡，改用硬切。",
        )

    if getattr(src, "scene_number", None) != getattr(shot, "scene_number", None):
        return ContinuityDecision(
            False, None, 1,
            f"分鏡表要接 {src_id}，但那是第 {src.scene_number} 場、"
            f"這一鏡是第 {shot.scene_number} 場 —— 換場景不接幀，改用硬切。",
        )

    # 引擎接幀時參考卡照餵（本機 H3）→ 每張臉都有自己的卡，臉的依據不只末幀那一格，不必為臉斷鏈
    identity_reason = None if cards_with_frame else _identity_break(shot, src)
    if identity_reason:
        # 同場次、但臉的依據不足：斷鏈帶卡，末幀降級成場景參考圖
        # （scene_anchor_source），場景不靠一句提示詞裸撐。
        return ContinuityDecision(
            False, None, 1,
            identity_reason + " 場景仍是同一個，帶上一鏡末幀當場景參考。",
            same_scene_break=True,
            scene_anchor_source=src_id,
        )

    positions = _chain_positions(shots, max_chain, cards_with_frame)
    position = positions.get(shot.shot_id, 1)
    if position == 1:
        # 這裡的斷點跟「跨場次」的斷點意義完全不同：
        #    跨場次是**應該**換場景，鏈太長是**不該**換場景但被迫斷開。
        #    參考圖只鎖角色長相不鎖場景，所以斷開之後模型會重新想像
        #    一個場景（實際觀察到成品換到別的場景）。
        #    note 要講清楚是後者，呼叫端才知道要在提示詞裡補場景延續。
        return ContinuityDecision(
            False, None, 1,
            f"接幀鏈已經連了 {max_chain} 鏡，再接下去畫面會愈漂愈遠 —— "
            f"這一鏡改用角色參考圖重新錨定長相。"
            f"場景仍是同一個，帶上一鏡末幀當場景參考。",
            same_scene_break=True,
            scene_anchor_source=src_id,
        )

    return ContinuityDecision(
        True, src_id, position,
        f"✓ 接上 {src_id} 的末幀（鏈上第 {position}/{max_chain} 鏡）。",
    )


def _facing(shot) -> str | None:
    """表演句寫「X…正面對著鏡頭」的那個 X（沒寫就是 None）。

    找含「正面對著鏡頭」的那一句，取這一鏡的角色裡最先出現在那句的人 ——
    「月音站在洗手台前、正面對著鏡頭」要回月音，不是「月音站在洗手台前」。"""
    text = getattr(shot, "performance", None) or getattr(shot, "description", None) or ""
    clause = next((c for c in re.split(r"[，。；]", text) if "正面對著鏡頭" in c), "")
    found = [(clause.find(n), n) for n in (shot.characters_in_frame or []) if n and n in clause]
    return min(found)[1] if found else None


def borrow_anchor(shot, shots: list, clips: list) -> str | None:
    """斷鏈的兩人鏡，借「同站位、已通過」那一鏡的末幀當首幀；找不到回 None。

    為什麼：沒首幀的鏡只剩空景卡撐，模型會跑景（s061_07 跑到戶外樹下、13 長出百葉窗和盆栽、
    10 背景冒出馬桶）。借一張同樣是「X 正面、Y 背對」的已通過末幀，第 61 場 03／13／14／16／17b 五鏡都一次過
    （正式管線症狀表：「不連幀、給同景別首幀」）。
    條件全部要對：同一場、同一組人、同一個人正面對鏡頭、景別差一級以內。少一個就不借 —— 借錯的首幀比沒首幀更糟。
    """
    from ..director import gates
    from ..schemas.segment import ClipStatus

    who = _facing(shot)
    if getattr(shot, "first_frame_from", None) or len(shot.characters_in_frame or []) < 2 or not who:
        return None
    done = {c.shot_id for c in clips if c.status == ClipStatus.APPROVED and c.uri}
    rank = gates.SIZE_RANK
    order = [s.shot_id for s in shots]
    here = order.index(shot.shot_id) if shot.shot_id in order else len(order)
    cands = [s for s in shots
             if s.shot_id in done and s.shot_id != shot.shot_id
             and s.director_scene_id == shot.director_scene_id
             and set(s.characters_in_frame or []) == set(shot.characters_in_frame)
             and _facing(s) == who
             and abs(rank.get(s.shot_type.value, 1) - rank.get(shot.shot_type.value, 1)) <= 1]
    # 越近越好（站位、情緒最接近）；前面的優先於後面的
    cands.sort(key=lambda s: (order.index(s.shot_id) > here, abs(order.index(s.shot_id) - here)))
    return cands[0].shot_id if cands else None
