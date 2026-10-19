"""H3（MiniMax H3，本機 ComfyUI）的提示詞組法 —— 從 nemotron-director 的 render.py 原樣搬來，一個字都不改。

為什麼不改：這套六行寫法跟作者正式製作實際送出的提示詞逐字比對過（第 29 場 11 鏡、第 61 場 8 鏡一致），
運鏡措辭是本機管線實測過的句子。換引擎（Google 的雲端影片模型）就另寫一個 engines/xxx_prompt.py，不在這裡加分支。
"""

import json
import os
import random
import time
import uuid

SHOT_ZH = {"WIDE": "全景", "MEDIUM": "中景", "MEDIUM_CLOSE": "中特寫", "CLOSE_UP": "特寫"}
# 運鏡措辭沿用本機管線驗證過的寫法；新增運鏡前要先實測，不要自己編一句
MOVE_ZH = {
    "STATIC": "鏡頭固定不動",
    "BREATHE": "鏡頭不推不拉，只有極輕微的手持呼吸感",
    "DOLLY_IN": "鏡頭很慢地往前推近，推到定位就停住",
    "DOLLY_OUT": "鏡頭很慢地往後拉開，拉到定位就停住",
    "PUSH_FACE": "鏡頭很慢地朝畫面裡那個人推近，推到那個人的臉和肩膀佔滿畫面就停住",
    "PAN_RETURN": "鏡頭慢慢橫搖過去，停一下，再慢慢搖回來",
    "TILT_UP": "鏡頭很慢地往上搖",
    "TILT_DOWN": "鏡頭很慢地往下搖",
    "PUSH_RETURN": "鏡頭先慢慢推近，停一下，再慢慢退回原位",
    "ZOOM": "鏡頭位置不動，畫面慢慢拉近，背景跟著壓縮",
    "PULL_FOCUS": ("機位完全不動，只有景深在變：一開始畫面裡那個人身後的東西都看得出形狀，"
                   "接著它們一點一點化開、輪廓融掉，變成柔軟的色塊和圓形的光斑，到最後只有那個人是清楚的"),
    "TRACK": "鏡頭配合她走路的速度一路後退，她在畫面裡的大小不變",
    "FOLLOW": "鏡頭跟著她走的方向慢慢橫移，她一直在畫面中間",
}
# 0.92 MP（1312×736）＝語速公式校準時的解析度；換解析度 gates 的秒數就不準了
MEGAPIXELS = float(os.environ.get("RENDER_MEGAPIXELS", "0.92"))


def build_prompt(scene: dict, shot: dict) -> str:
    """組出 H3 的六行提示詞：風格／人物／場景／鏡頭／表演／口白（沒台詞就給環境聲）。

    場景行跟著「站位」（spot）走，不是跟著房間走：同一間房，在書桌前的近景只該描述
    她身後看得到的書架，全景才描述整間房 —— 近景寫了窗戶和床，模型就會想辦法把它們塞進來。
    （跟本機管線實際送出的提示詞逐字比對過，第 29 場 11 鏡一致。）
    沒台詞的鏡一定要給一行聲音：空著，模型會自己編一句話填進去（律一）。
    """
    return render_plan(scene, shot)[0]


def render_plan(scene: dict, shot: dict) -> tuple:
    """回傳 (提示詞, 參考圖清單)。

    場景有設參考圖（render_zh.refs）就走參考圖寫法 —— 跟正式製作現在的四步參考圖工作流同一套：
    人物行／場景行只寫「就是參考圖 <Picture N> 裡的那個人／地方」，長相、衣服、擺設交給圖，
    文字不再描述一次（一件事只能有一個定義者：圖跟文字都講，模型就多一個重畫的理由）。
    時間留在場景行 —— 時間不是圖管得到的（實測拿掉會整支變白天）。
    參考圖清單是邏輯名稱，實際檔案路徑只在家裡那台渲染節點上。
    """
    rz = scene["render_zh"]
    who = [c for c in shot.get("characters_in_frame") or [] if c in rz["characters"]]
    spot_key = shot.get("spot") if shot.get("spot") in rz["spots"] else next(
        k for k, v in rz["spots"].items() if not who or v["location"] == scene["characters"][who[0]]["location"])
    spot = rz["spots"][spot_key]
    loc = spot["location"]
    refs = rz.get("refs")
    images = []

    def fill(template, keys):
        """把一段「參考圖 <Picture {n0}>…<Picture {n1}>」的文字填上這一鏡的圖號（圖照出現順序編號、重複的不重編）。"""
        keys = [keys] if isinstance(keys, str) else keys
        nums = []
        for k in keys:
            if k not in images:
                images.append(k)
            nums.append(images.index(k) + 1)
        return template.format(n=nums[0], **{f"n{i}": v for i, v in enumerate(nums)})

    if refs:
        rc = refs["characters"]
        texts = {}

        def as_text(c):
            # 沒有參考卡的人只能靠文字：名字一定要在（場面調度的外貌句常常只寫「一位女性」，模型分不出是誰）
            name = ((scene.get("characters") or {}).get(c) or {}).get("name") or c
            look = rz["characters"].get(c, "")
            return f"{name}，{look}" if look and not look.startswith(name) else (look or name)

        # 圖的順序照正式製作（場景 29 月音那邊場景圖排第一；深息、場景 61 人物排第一）
        for kind in refs.get("order", {}).get(loc, ["person", "spot"]):
            if kind == "spot":
                sp = refs["spots"].get(spot_key)
                # 這個地方沒有空景卡（沒準備、沒上傳）→ 只寫文字（local.prompt_for 會標「很隨機」的警告）
                texts["spot"] = fill(sp["zh"], sp["image"]) if sp else spot["zh"]
            elif len(who) == 1 and who[0] in rc:
                texts["person"] = fill(rc[who[0]]["zh"], rc[who[0]]["image"])
            elif len(who) <= 1:
                texts["person"] = as_text(who[0]) if who else ""
            else:
                # 兩個人同框：正式製作的寫法是「畫面裡有兩個人。A，就是圖1；她旁邊是／跟她面對面的是 B，就是圖2。」
                # 站位決定關係句：吵架、對質是面對面（正式製作第 61 場「跟她面對面的是 Lisa」），不是「她旁邊是」
                # 三個人以上：第三世 s001_01 璃安＋塔列昂＋ぷく，舊版只寫前兩個 → ぷく沒有卡、被畫成小白鬼。
                #    參考圖上限 9 張（MiniMaxH3ReferenceToVideo 的 ref_images max=9，/object_info 問的）：
                #    空景 2 張，剩下 7 張給人 —— 超過的人只給全身卡一張。沒有卡的人（沒選角）只寫文字。
                pair = {"face_to_face": "跟她面對面的是", "side_by_side": "她旁邊是", **refs.get("pair", {})}
                rel = pair.get(shot.get("blocking") or "side_by_side", "她旁邊是")
                budget = 7

                def one(c):
                    nonlocal budget
                    if c not in rc:
                        return as_text(c)
                    imgs = rc[c]["pair_image"]
                    if len(imgs) <= budget:
                        budget -= len(imgs)
                        return fill(rc[c]["pair_zh"], imgs)
                    budget -= 1
                    return fill(rc[c]["pair_zh"].split("，")[0] + "，就是參考圖 <Picture {n0}> 裡的那個人", [imgs[0]])

                count = {2: "兩", 3: "三", 4: "四"}.get(len(who), str(len(who)))
                parts = [one(c).rstrip("。") for c in who]      # 照順序算（圖的名額先給前面的人）；每段句號拿掉，最後補一個
                texts["person"] = (f"畫面裡有{count}個人。{parts[0]}；{rel} {parts[1]}"
                                   + "".join(f"；還有 {p}" for p in parts[2:]) + "。")
        person, place = texts["person"], texts["spot"]
    else:
        rc = {}
        person = "".join(rz["characters"][c] for c in who)
        place = spot["zh"]
    lines = [
        rz["style"],
        "人物：" + person,
        "場景：" + place,
        f"鏡頭：{SHOT_ZH.get(shot.get('shot_type'), '中景')}，{MOVE_ZH.get(shot.get('camera_move'), MOVE_ZH['BREATHE'])}。",
        "表演：" + (shot.get("visual_prompt") or ""),
    ]
    spoken = shot.get("dialogue_lines") or ([{"speaker": shot.get("speaker"), "text": shot["dialogue"]}] if shot.get("dialogue") else [])
    if shot.get("voice_role") == "offscreen" and spoken:
        # 畫面外的聲音：講明「畫面裡的人沒有說話」，不然模型會讓畫面裡的人對嘴
        sp = spoken[0]["speaker"]
        voice = rc.get(sp, {}).get("offscreen_voice", f"{scene['characters'][sp].get('name_zh', sp)}的聲音")
        # 同一個人在畫面外連講幾句（正式製作第 61 場第一鏡：Lisa 在門外講兩句）→ 接成一段，不要只送第一句
        said = "".join(x["text"] for x in spoken if x.get("speaker") == sp) or spoken[0]["text"]
        lines.append(f"聲音：畫面外傳來 {voice}，普通話口白︰「{said}」（畫面裡的人沒有說話）")
    elif spoken:
        # 同框兩個人以上：每一句標明「誰」講，用看得到的特徵（黃毛衣／黑框眼鏡）而不是只寫名字 —— 模型不認得名字，認得衣服
        #    心聲（narration）不標：有主詞嘴就會動（s001_06 實測），心聲要的正是「有聲音、嘴閉著」
        inner = shot.get("voice_role") == "narration"
        for ln in spoken:
            tag = rc.get(ln["speaker"], {}).get("voice_tag") if len(who) > 1 and not inner else None
            lines.append(f"{tag}的普通話口白︰「{ln['text']}」" if tag else f"普通話口白︰「{ln['text']}」")
    else:
        lines.append("聲音：" + rz.get("ambience", {}).get(loc, "很安靜，只有很輕的環境底噪。"))
    return "\n".join(lines), images
