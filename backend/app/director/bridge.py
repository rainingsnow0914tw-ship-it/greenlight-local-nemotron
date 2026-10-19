"""導演迴圈的分鏡 → Greenlight 的鏡頭（schemas/shot.py）。

為什麼要轉：Greenlight 後面的提示詞頁、試拍、批次生成、選版本、接片都吃它自己的 Shot；
導演迴圈吃的是作者管線那一套（站位、精細運鏡、表演句、任意秒數）。兩邊都留著：
- Greenlight 的大類欄位（shot_type、camera_move、base_duration_seconds）照填 —— 它的統計、成功率預測、雲端引擎要用
- 精細的放在本機引擎專用欄位（engine_camera_move、engine_duration_seconds、performance…）—— H3 提示詞照這些寫
"""

from __future__ import annotations

import re

from ..schemas.shot import CameraMove, Shot, ShotType
from . import gates

# 導演迴圈的精細運鏡 → Greenlight 的大類（只給統計用；生成照精細的寫）
_MOVE = {
    "STATIC": CameraMove.STATIC, "BREATHE": CameraMove.HANDHELD, "PULL_FOCUS": CameraMove.STATIC,
    "DOLLY_IN": CameraMove.DOLLY, "DOLLY_OUT": CameraMove.DOLLY, "PUSH_FACE": CameraMove.DOLLY,
    "PUSH_RETURN": CameraMove.DOLLY, "ZOOM": CameraMove.ZOOM, "PAN_RETURN": CameraMove.PAN,
    "TILT_UP": CameraMove.TILT, "TILT_DOWN": CameraMove.TILT, "TRACK": CameraMove.TRACKING,
    "FOLLOW": CameraMove.TRACKING,
}
_VOICE = {"character": "character", "offscreen": "offscreen", "narration": "narrator"}


def _base_duration(seconds: float) -> int:
    """Greenlight 的 base_duration 只能 4／6／8／10（雲端引擎的硬限制）：取不短於實際長度的那一檔。"""
    return next((d for d in (4, 6, 8, 10) if d >= seconds), 10)


def to_greenlight_shots(dscene: dict, dshots: list[dict], scene_number: int) -> list[Shot]:
    """一場的導演分鏡 → Greenlight Shot 清單。角色代號（luna）換回劇本裡的名字（月音）。"""
    names = {cid: c.get("name") or cid for cid, c in (dscene.get("characters") or {}).items()}

    def zh_names(text: str | None) -> str | None:
        """中文表演句裡模型自己拼的代號／拼音（「看向 Yueyin」）換回劇本名字 —— 渲染模型不認得 Yueyin 是誰。"""
        for cid, c in (dscene.get("characters") or {}).items():
            zh = c.get("name_zh") or c.get("name")
            if text and zh and cid != zh:
                text = re.sub(rf"(?i)(?<![A-Za-z]){re.escape(cid)}(?![A-Za-z])", zh, text)
        return text

    dshots = [{**s, "visual_prompt": zh_names(s.get("visual_prompt"))} for s in dshots]
    out = []
    for i, s in enumerate(dshots, 1):
        seconds = float(s.get("duration_seconds") or 6)
        move = s.get("camera_move") or "STATIC"
        role = s.get("voice_role") or "character"
        out.append(Shot(
            shot_id=s.get("shot_id") or f"s{scene_number:03d}_{i:02d}",
            scene_number=scene_number,
            order_in_scene=i,
            base_duration_seconds=_base_duration(seconds),
            shot_type=ShotType(s["shot_type"]) if s.get("shot_type") in ShotType.__members__ else ShotType.MEDIUM,
            camera_move=_MOVE.get(move, CameraMove.STATIC),
            description=s.get("visual_prompt") or "",
            visual_prompt=s.get("visual_prompt_en") or None,
            characters_in_frame=[names.get(c, c) for c in s.get("characters_in_frame") or []],
            dialogue=s.get("dialogue") or None,
            voice_role=_VOICE.get(role, "character"),
            speaker=names.get(s.get("speaker"), s.get("speaker")) if s.get("dialogue") else None,
            engine_duration_seconds=seconds,
            engine_camera_move=move,
            spot=s.get("spot"),
            blocking=s.get("blocking"),
            performance=s.get("visual_prompt"),
            dialogue_lines=[{"speaker": names.get(d.get("speaker"), d.get("speaker")), "text": d.get("text", "")}
                            for d in s.get("dialogue_lines") or []],
            director_scene_id=dscene.get("scene_id"),
        ))
    # 接幀只給「馬上接連戲動作」的鏡（作者管線的做法）：剪接上的前一鏡、同一個地點、同一批人
    # → 動作直接延續，接上一鏡末幀。換人、換地點＝中間有過渡，不接（接了只會把姿勢和漂移帶下去）。
    # 比的是站位所在的地點、不是站位代號：同一個洗手台區分成 A、B 兩側，一鏡到底時鏡頭換一側仍是連續的。
    # 鏈長上限與跨場次由 services/continuity.decide 在生成時再擋一次。
    spots = (dscene.get("render_zh") or {}).get("spots") or {}
    where = lambda sh: (spots.get(sh.spot) or {}).get("location") or sh.spot
    for prev, cur in zip(out, out[1:]):
        if (cur.spot and where(cur) == where(prev)
                and set(cur.characters_in_frame) == set(prev.characters_in_frame)
                and cur.characters_in_frame
                and not gates.chain_problem(prev.shot_type.value, cur.shot_type.value,
                                            prev.characters_in_frame, cur.characters_in_frame)):
            cur.first_frame_from = prev.shot_id
    return out
