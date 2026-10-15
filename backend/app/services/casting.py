"""選角關卡的邏輯層 —— 重抽方向、成本追蹤、閘門判定。

設計理由：
    這個模組跟 cost_engine 一樣，刻意不呼叫任何 AI。
    它負責的是「這一關的規則」：給哪些按鈕、花了多少錢、能不能往下走。
    真正產生圖片的是 character_designer agent，那是另一個模組的事。

    分開的好處：整關的流程邏輯可以離線測試，
    不需要真的燒 Imagen 的錢就能驗證「五輪之後會不會正確跳警告」。
"""

from __future__ import annotations

from pydantic import BaseModel

from ..schemas.casting import (
    CastingGate,
    CastingRound,
    CastingSession,
    CharacterCandidate,
    CharacterKind,
)
from . import config_loader, i18n


class RerollDirection(BaseModel):
    """一個重抽方向按鈕。

    label / hint 帶雙語 —— 前端拿到兩種語言後自己決定顯示哪個，
    切換語言不需要重打 API（見 services/i18n.py 的說明）。
    """

    direction_id: str
    label: dict[str, str]
    hint: dict[str, str]
    prompt_modifier: str
    # 造型模式（段落造型變體）可不可以用這個方向。
    # 造型模式的前提是「臉不動」—— 更年輕／更成熟這類改臉的方向在那裡出現，
    #    按下去就是同一段的角色換了一張臉。哪些方向保臉，由 casting.yaml 標，不寫死在 UI。
    look_variant: bool = False


class BudgetNotice(BaseModel):
    """成本提示。

    是「提示」不是「阻擋」—— 這是使用者的錢，也是他的創作決定。
       系統該提供資訊，不該替他喊停。
    """

    level: str  # none / soft / hard
    message: str = ""
    spent_usd: float = 0.0
    rounds_used: int = 0


def _casting_config() -> dict:
    return config_loader.casting()


def available_directions(kind: CharacterKind) -> list[RerollDirection]:
    """列出這個角色可以按的重抽方向。

    依角色類型過濾：對一個沒有身體的 AI 顯示「更年輕／更成熟」
    是廢話按鈕，會讓使用者覺得系統根本沒讀懂他的劇本。

    「換一批」永遠排第一（order: 0），因為那是最直覺的動作 ——
    使用者說不出哪裡不對的時候，至少還能再抽一次。
    """
    cfg = _casting_config()
    result: list[RerollDirection] = []

    for item in cfg.get("directions", []):
        applies = item.get("applies_to", "any")
        if applies not in ("any", kind.value):
            continue
        result.append(
            RerollDirection(
                direction_id=item["id"],
                label=i18n.pair(item, "label"),
                hint=i18n.pair(item, "hint"),
                prompt_modifier=item.get("prompt_modifier", ""),
                look_variant=bool(item.get("look_variant", False)),
            )
        )

    # order 欄位只用來把「換一批」釘在最前面，其餘維持 YAML 的順序 ——
    # 那個順序是有意義的（成對的方向相鄰：更年輕/更成熟、更柔和/更銳利）。
    result.sort(key=lambda d: 0 if d.direction_id == "reroll" else 1)
    return result


def round_cost_usd() -> float:
    """一輪要花多少錢。

    候選圖一律用 Imagen 4 Fast —— 選擇階段不需要極致畫質，
    能分辨長相差異就夠了。選定之後才用 Standard 重生正式版。
    """
    cfg = _casting_config()
    per_round = int(cfg.get("candidates_per_round", 3))
    pricing = config_loader.pricing()
    fast = float(
        pricing.get("imagen", {}).get("tiers", {}).get("fast", {}).get("per_image_usd", 0.02)
    )
    return round(per_round * fast, 3)


def budget_notice(session: CastingSession) -> BudgetNotice:
    """依累計花費決定要不要提示。

    兩段式：
      soft  超過建議輪數 → 顯示已花多少，讓他心裡有數
      hard  超過金額上限 → 提醒問題可能出在角色描述本身

    抽到十輪還抽不到想要的，通常代表描述太模糊或方向不對，
    繼續抽只是在燒錢。這時候該建議他換一個方向按鈕，
    而不是繼續按「換一批」。
    """
    cfg = _casting_config().get("budget", {})
    soft_after = int(cfg.get("soft_warn_after_rounds", 5))
    hard_usd = float(cfg.get("hard_warn_usd", 0.60))

    spent = session.total_spent_usd
    rounds = session.rounds_used

    if spent >= hard_usd:
        return BudgetNotice(
            level="hard",
            spent_usd=spent,
            rounds_used=rounds,
            message=(
                f"這個角色已經抽了 {rounds} 輪、花了 ${spent:.2f}。"
                f"抽到這個程度通常代表角色描述本身不夠明確 —— "
                f"試試換一個方向按鈕，或回頭確認劇本裡對這個角色的描寫。"
            ),
        )

    if rounds >= soft_after:
        return BudgetNotice(
            level="soft",
            spent_usd=spent,
            rounds_used=rounds,
            message=f"已經抽了 {rounds} 輪，花費 ${spent:.2f}。可以繼續，也可以回頭選前面幾輪的圖。",
        )

    return BudgetNotice(level="none", spent_usd=spent, rounds_used=rounds)


def add_round(
    session: CastingSession,
    candidates: list[CharacterCandidate],
    direction_id: str = "reroll",
    segment_order: int | None = None,
) -> CastingRound:
    """把新生成的一輪加進 session。

    已鎖定的 session 不能再加**一般**輪次。
       選定角色之後又改長相，會讓已經生成的鏡頭全部失效 ——
       那是最貴的錯誤，所以在資料層就擋掉，不靠 UI 記得禁用按鈕。

    段落造型變體（segment_order 有值）剛好相反：**必須先鎖定**才能生 ——
       造型是拿鎖定的臉當輸入去換衣服，沒有臉就沒有「同一個人換衣服」這回事。
    """
    if segment_order is None and session.is_locked:
        raise ValueError(
            f"角色「{session.character_name}」已經選定並鎖定，不能再重抽。"
            f"要改的話必須先解除選定，並確認尚未開始生成鏡頭。"
        )
    if segment_order is not None and not session.is_locked:
        raise ValueError(
            f"角色「{session.character_name}」還沒選定。"
            f"段落造型是「同一張臉換衣服」，要先鎖定角色本體才能生。"
        )

    label = "初次生成"
    for item in _casting_config().get("directions", []):
        if item["id"] == direction_id:
            label = item["label"]
            break

    rnd = CastingRound(
        round_number=len(session.rounds) + 1,
        direction_id=direction_id,
        direction_label=label,
        candidates=candidates,
        segment_order=segment_order,
    )
    session.rounds.append(rnd)
    return rnd


def select(session: CastingSession, candidate_id: str) -> CharacterCandidate:
    """選定一張候選圖，鎖住這個角色。

    可以選任何一輪的圖，不限最後一輪 ——
    抽了五輪之後常常會覺得「還是第二輪那張好」，
    不讓他回頭選等於逼他重抽，那是白花錢。
    """
    match = next(
        (c for c in session.all_candidates() if c.candidate_id == candidate_id), None
    )
    if match is None:
        raise ValueError(
            f"角色「{session.character_name}」沒有候選圖 {candidate_id}。"
        )
    session.selected_candidate_id = candidate_id
    # 第一次鎖定 = 這張臉定了。沒有身份參考的角色，把選定那張當身份參考，
    #    之後每次重生（換服裝／換髮型／段落造型）都拿它當圖片輸入，臉才不會跑。
    #    使用者自己上傳的（uploaded）優先，不覆蓋。
    if not session.identity_reference_uri and match.image_uri:
        session.identity_reference_uri = match.image_uri
        session.identity_source = "generated"
    return match


def unlock(session: CastingSession) -> None:
    """解除選定，讓使用者可以重新挑。

    只在「尚未開始生成任何鏡頭」時允許 ——
    這個前置條件由呼叫端負責檢查（它才知道生成狀態）。
    這裡只負責清掉選定狀態。
    """
    session.selected_candidate_id = None
    session.final_reference_uri = None
    # 解除選定 = 「我不要這張臉」。自動取用的身份參考跟著清掉，
    # 上傳的（uploaded）是使用者的意思，保留。段落造型是掛在這張臉上的，一起清。
    if session.identity_source == "generated":
        session.identity_reference_uri = None
        session.identity_source = None
    session.segment_looks = {}


def select_look(
    session: CastingSession, segment_order: int, candidate_id: str
) -> CharacterCandidate:
    """把某張候選設成第 N 段的造型。$0。不影響鎖定狀態、不動 selected_candidate_id。

    影片生成端用 `session.look_for(segment_order)` 取用，規則只放 schema 一處。
    """
    if not session.is_locked:
        raise ValueError(
            f"角色「{session.character_name}」還沒選定，不能設段落造型。"
        )
    match = session.candidate(candidate_id)
    if match is None or not match.image_uri:
        raise ValueError(
            f"角色「{session.character_name}」沒有候選圖 {candidate_id}。"
        )
    session.segment_looks[str(segment_order)] = candidate_id
    return match


def clear_look(session: CastingSession, segment_order: int) -> bool:
    """第 N 段回到全片預設造型。回傳原本有沒有覆寫。"""
    return session.segment_looks.pop(str(segment_order), None) is not None


def set_identity(
    session: CastingSession, image_uri: str, *, source: str, consent: dict | None
) -> None:
    """設定身份參考（臉）。

    上傳來的一定要帶 consent —— 使用者上傳的臉，責任歸屬在使用者，
       所以免責聲明必須被確認過。route 層先擋，這裡再擋一次，兩層都不靠 UI 記得。
    """
    if source == "uploaded" and not consent:
        raise ValueError("上傳身份參考必須先確認免責聲明。")
    session.identity_reference_uri = image_uri
    session.identity_source = source
    session.identity_consent = consent if source == "uploaded" else None


def identity_uri(session: CastingSession) -> str | None:
    """這個角色現在的臉是哪張圖。

    明確設定的（上傳／首次鎖定自動取用）優先；沒有但已鎖定的角色，
    退回選定那張 —— 這條是給「身份參考」欄位加入之前就鎖定的舊資料用的：
    它們鎖定時還沒有這個概念，不補這條就會在段落造型時沒有臉可餵、
    臉跑掉，而且看起來像功能壞了。**所有要「拿臉」的地方一律走這裡**，不要各自判斷。
    """
    if session.identity_reference_uri:
        return session.identity_reference_uri
    chosen = session.selected
    return chosen.image_uri if (chosen and chosen.image_uri) else None


def clear_identity(session: CastingSession) -> None:
    session.identity_reference_uri = None
    session.identity_source = None
    session.identity_consent = None


def gate_status(gate: CastingGate) -> dict:
    """關卡狀態摘要，給 UI 直接用。

    blocked_reason 是關鍵欄位：按鈕被鎖住時，
       一定要明確說出還缺誰。只顯示一個灰掉的按鈕而不說原因，
       使用者會以為系統壞了。
    """
    pending = gate.pending_characters
    return {
        "can_proceed": gate.can_proceed,
        "pending_characters": pending,
        "blocked_reason": (
            ""
            if gate.can_proceed
            else f"還有 {len(pending)} 個角色沒選定：{'、'.join(pending)}"
        ),
        "total_spent_usd": gate.total_spent_usd,
        "characters_locked": sum(1 for s in gate.sessions if s.is_locked),
        "characters_total": len(gate.sessions),
    }
