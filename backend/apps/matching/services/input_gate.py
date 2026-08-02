"""輸入閘門（Input Gate）— LLM 呼叫之前的純規則過濾。

問題：使用者送出 `54`、`6456`、`asdasd` 這類輸入時，舊流程照樣跑完
RAG 檢索 + Claude API，並把該則訊息寫進 `DialogueSessionRecord.session_state`
與 `AIConversation`。之後每一輪都重新攜帶這些垃圾內容進 prompt，
token 消耗隨輪次呈平方級成長。

這個模組在 LLM 之前攔截，命中時由呼叫端回覆靜態字串，零 API 成本。

設計限制（刻意的）：
- 純 Python 規則，不呼叫任何外部 API、不載入 transformer 模型。
  單次判斷在 microsecond 級完成，可以放在 WebSocket 的同步路徑上。
  （唯一的外部 import 是 chat.services.filter 的單字粗口比對，
  那也只是一個 frozenset 查表。）
- 所有 fallback 都是模組常數，**不得**用 LLM 生成。

── 規則 0（單字粗口）為什麼不看 prev_ai_is_question ────────────────
「好」與「幹」的長度一樣，但脈絡敏感度相反：AI 拋出提問後，「好」是
合法的對話輪次；「幹」不會因為 AI 剛問了問題就變成回答。所以規則 0
排在規則 1 之前且無條件攔截，跟規則 5 的脈絡判斷完全分開。

粗口的字彙留在 chat.services._blacklist（單一維護點），這裡只是套用。

── 規則 1 與規則 5 的關係 ──────────────────────────────────────────
規格表把規則 1（SHORT_VALID 白名單）寫成「無條件放行」，但同一份規格的
測試案例又要求「好」（`prev_ai_is_question=False`）必須攔截，而「好」正是
白名單的第一個項目。兩者不可能同時成立。

這裡採用後者：白名單命中**豁免規則 2/3/4（非語言性判斷）**，但**規則 5 仍然適用**。
理由是規格自己指明規則 5 是本次設計的核心——同一則「好」，在 AI 剛拋出
提問後是合法對話輪次，在無提問脈絡下憑空出現才是低訊息量輸入。若白名單
無條件放行，規則 5 對白名單字詞永遠不會觸發，核心設計就成了死碼。

白名單真正吃重的地方在 `is_substantive_message()`：短回應即使放行進 LLM，
對語義分析只有稀釋作用，必須排除在離題偵測／論述移動度／僵局偵測之外。
"""

import re
from enum import Enum

from chat.services.filter import find_standalone_profanity

# ═══════════════════════════════════════════════════════════
# Thresholds
#
# 以下數值全部是**工程性防禦門檻**，不是經校準的實驗參數。
# 它們的唯一目的是擋掉明顯非對話的輸入以控制 token 成本，
# 不應被當成任何去極化指標的一部分，也不需要寫進論文方法章節。
# 調整時只需確認 tests/test_input_gate.py 的邊界案例仍成立。
# ═══════════════════════════════════════════════════════════

# 字元重複度下限：unique(t)/len(t) 低於此值視為敲鍵盤（`aaaaaaa`、`abababab`）。
# 0.3 允許中文常見的字詞重複（「我覺得我覺得」約 0.5），但擋掉單字元灌水。
REPETITION_RATIO_THRESHOLD = 0.3

# 語意字元（CJK + 拉丁字母）佔比下限。低於此值代表訊息主體是數字與符號，
# 例如 `a123456789`（0.1）。0.4 讓「核能 2025 年 30% 佔比」這類數據句仍能通過。
SEMANTIC_CHAR_RATIO_THRESHOLD = 0.4

# 觸發重複度檢查所需的最小長度。太短的字串 unique 比例天然偏低，
# 例如「好的」= 1.0、「呵呵」= 0.5，不套用重複度判準才不會誤傷。
REPETITION_MIN_LENGTH = 4

# 低訊息量長度門檻：短於此長度且前一輪 AI 沒有提問 → 視為低訊息量輸入。
# 長度本身不是獨立判準，一定要搭配 prev_ai_is_question 才成立。
LOW_INFORMATION_MAX_LENGTH = 4

# 進入 NLP 語義分析（離題／論述移動／僵局）所需的最小長度。
# 與 LOW_INFORMATION_MAX_LENGTH 同值但語意不同：前者決定「要不要回應」，
# 這個決定「要不要拿去算向量」。分開命名以免日後只想調其中一個時綁死。
SUBSTANTIVE_MIN_LENGTH = 4

# ── 遞進節流 ────────────────────────────────────────────────
# 連續無效輸入的呈現方式分級。數字為 invalid_input_count 的上界（含）。
THROTTLE_BUBBLE_MAX_COUNT = 2      # 1–2：靜態 fallback，正常對話氣泡
THROTTLE_NOTICE_MAX_COUNT = 5      # 3–5：系統提示列，不佔對話輪數
COOLDOWN_SECONDS = 60              # ≥6：輸入框停用 60 秒

# ── Per-user rate limit（獨立於內容判斷之外，防自動化灌訊息）──────
MIN_SEND_INTERVAL_SECONDS = 1.5
MAX_MESSAGES_PER_MINUTE = 20


CJK = re.compile(r"[一-鿿]")
LATIN = re.compile(r'[A-Za-z]')

# 句尾標點與空白，比對白名單前先剝除（「好的。」→「好的」）。
_TRAILING_PUNCT = "。．.，,、！!？?～~…・;；:：　 \t\r\n"

SHORT_VALID = {
    '好', '好的', '可以', '沒問題', '嗯', '對', '沒錯', '同意', '了解',
    '不', '不要', '不行', '不同意', '不太同意', '不確定', '不知道',
    'ok', 'okay', '也許', '大概吧', '再說', '換一個', '繼續', '為什麼',
}


class InputVerdict(Enum):
    """閘門判定結果。每種攔截類型對應不同的回覆語氣。"""

    VALID = "valid"
    # 規則 0：整則訊息只有單字粗口（「幹」「幹幹幹」「幹！！！」）。
    PROFANITY_ONLY = "profanity_only"
    # 規則 2/3/4：純數字／符號、敲鍵盤、語意字元佔比過低。
    NON_LINGUISTIC = "non_linguistic"
    # 規則 5：內容合法但過短，且沒有 AI 提問作為脈絡。
    LOW_INFORMATION = "low_information"


# ═══════════════════════════════════════════════════════════
# 靜態 fallback（零 token）
#
# 一律採澄清式追問語氣。舊版那句「這場對話裡你的輸入一直是無意義的字串，
# 我沒有辦法承接任何實質論點」帶指責性，在實驗情境下會污染受試者的
# Part E 平台體驗評估與 Part C-2 對話品質感知，已移除。
# ═══════════════════════════════════════════════════════════

FALLBACK_NON_LINGUISTIC = [
    "我看不出這則訊息想表達的意思，可以說說你對這個議題的想法嗎？",
    "這則輸入我沒辦法承接論點。你目前的立場是什麼？",
]
FALLBACK_LOW_INFORMATION = [
    "可以再多說一點嗎？我想知道你這樣想的理由。",
    "你指的是哪個部分？我們可以就那點深入談。",
]
# 單獨的粗口是情緒訊號，不是低訊息量輸入——用「可以再多說一點嗎」回應
# 會答非所問。這裡承接情緒再把話題導回議題，符合去極化平台的角色。
# 一樣不指責、不說教。
FALLBACK_PROFANITY_ONLY = [
    "看得出來你可能有點情緒。是這個議題的哪一部分讓你有這種感覺？",
    "如果剛才有哪句話讓你不太舒服，可以直接說是哪一點。",
]

# 節流與速率限制的提示語。同樣是靜態常數，同樣不得用 LLM 生成。
COOLDOWN_NOTICE = "系統暫停接收訊息 {seconds} 秒。回來後可以直接說說你對這個議題的看法。"
RATE_LIMIT_NOTICE_TOO_FAST = "訊息送得太快了，稍等一下再送出。"
RATE_LIMIT_NOTICE_TOO_MANY = "短時間內送出的訊息過多，請稍後再試。"


def _normalize_for_whitelist(text: str) -> str:
    return text.strip().lower().rstrip(_TRAILING_PUNCT)


def _semantic_char_count(text: str) -> int:
    return len(CJK.findall(text)) + len(LATIN.findall(text))


def classify(text: str, prev_ai_is_question: bool = False) -> InputVerdict:
    """判定一則使用者輸入。規則順序不可調換（見模組 docstring）。

    Args:
        text: 使用者原始輸入。
        prev_ai_is_question: 這個 session 上一則 AI 回覆是否以提問收尾。
            由 `AIConversation.ai_turn_is_question` 提供，不要在這裡
            事後用正則猜。
    """
    t = text.strip()
    if not t:
        return InputVerdict.NON_LINGUISTIC

    # 規則 0 — 整則訊息只有單字粗口。排在規則 1 之前且不看
    # prev_ai_is_question：AI 剛拋出提問並不會讓「幹」變成合法的回答，
    # 這一點跟「好」正好相反。剝除標點與空白後判斷，所以
    # 「幹」「幹幹幹」「幹！！！」「幹 幹 幹」「幹。」一律命中，
    # 而「幹嘛」「樹幹」「幹，核電根本是騙局」不受影響。
    if find_standalone_profanity(t) is not None:
        return InputVerdict.PROFANITY_ONLY

    # 規則 1 — 短回應白名單。豁免規則 2/3/4，不豁免規則 5。
    whitelisted = _normalize_for_whitelist(t) in SHORT_VALID

    if not whitelisted:
        # 規則 2 — 不含任何 CJK 也不含任何拉丁字母（純數字／符號）。
        if not CJK.search(t) and not LATIN.search(t):
            return InputVerdict.NON_LINGUISTIC

        # 規則 3 — 字元重複度過高。
        if len(t) >= REPETITION_MIN_LENGTH:
            if len(set(t)) / len(t) < REPETITION_RATIO_THRESHOLD:
                return InputVerdict.NON_LINGUISTIC

        # 規則 4 — 語意字元佔比過低。
        if _semantic_char_count(t) / len(t) < SEMANTIC_CHAR_RATIO_THRESHOLD:
            return InputVerdict.NON_LINGUISTIC

    # 規則 5 — 短輸入且沒有提問脈絡。長度本身不是獨立判準。
    if len(t) < LOW_INFORMATION_MAX_LENGTH and not prev_ai_is_question:
        return InputVerdict.LOW_INFORMATION

    return InputVerdict.VALID


def is_meaningless(text: str, prev_ai_is_question: bool = False) -> bool:
    """True 表示這則輸入應被攔截，不得進入 LLM 或對話 context。"""
    return classify(text, prev_ai_is_question) is not InputVerdict.VALID


def is_short_response(text: str) -> bool:
    """True 表示這是一則短回應（白名單命中或過短）。

    短回應可以合法進入 LLM，但對語義分析只有稀釋作用。
    """
    t = text.strip()
    if not t:
        return True
    if _normalize_for_whitelist(t) in SHORT_VALID:
        return True
    return len(t) < SUBSTANTIVE_MIN_LENGTH


def is_substantive_message(text: str) -> bool:
    """True 表示這則訊息可以拿去做語義分析。

    共用判斷，避免離題偵測／論述移動度／僵局偵測三處各自實作。
    條件：通過閘門（以有提問脈絡的寬鬆模式判定，長度另由
    `is_short_response` 把關）且不是短回應。
    """
    t = text.strip()
    if not t:
        return False
    if is_short_response(t):
        return False
    return classify(t, prev_ai_is_question=True) is InputVerdict.VALID


def fallback_message(verdict: InputVerdict, count: int = 1) -> str:
    """依攔截類型與累積次數選出靜態回覆。**不呼叫 LLM。**

    連續攔截時輪替不同句子，順帶讓前端去重不會把兩則不同的提示疊成一則。
    """
    pool = {
        InputVerdict.LOW_INFORMATION: FALLBACK_LOW_INFORMATION,
        InputVerdict.PROFANITY_ONLY: FALLBACK_PROFANITY_ONLY,
    }.get(verdict, FALLBACK_NON_LINGUISTIC)
    index = (max(count, 1) - 1) % len(pool)
    return pool[index]


def rate_limit_notice(reason: str | None) -> str:
    if reason == "too_many":
        return RATE_LIMIT_NOTICE_TOO_MANY
    return RATE_LIMIT_NOTICE_TOO_FAST


def throttle_tier(count: int) -> str:
    """依連續無效輸入次數決定呈現方式。

    'bubble'  1–2  靜態 fallback，正常對話氣泡
    'notice'  3–5  系統提示列（非 AI 對話氣泡），不佔對話輪數
    'cooldown' ≥6  60 秒冷卻，前端 disable 輸入框
    """
    if count <= THROTTLE_BUBBLE_MAX_COUNT:
        return "bubble"
    if count <= THROTTLE_NOTICE_MAX_COUNT:
        return "notice"
    return "cooldown"


# ── AI 回合是否以提問收尾 ────────────────────────────────────

# 第六節的回應結構代號。C = 視角翻轉型（結尾拋出視角翻轉提問），
# E = 承接深化型（規格明寫「全程不拋問題」）。A/B/D 不保證，需回退判斷。
_JUDGMENT_TYPE_ALWAYS_QUESTION = {"C"}
_JUDGMENT_TYPE_NEVER_QUESTION = {"E"}
_QUESTION_MARKS = "？?"


def ai_turn_is_question(reply: str, judgment: str = "") -> bool:
    """這一輪 AI 回應是否以提問收尾。

    優先讀策略層：`<judgment>` 區塊格式為 `判定|型別代號|理由`，型別代號
    C（視角翻轉型）必然以提問收尾，E（承接深化型）必然不提問。

    A/B/D 型與判定段缺失／格式異常時退回句尾問號判斷。
    # TODO: 改由策略層提供 —— A/B/D 型目前無法從 judgment 得知本輪是否
    # 收在引導式提問，需要在 prompt 第十節的短碼中補一個欄位。
    """
    code = _judgment_type_code(judgment)
    if code in _JUDGMENT_TYPE_ALWAYS_QUESTION:
        return True
    if code in _JUDGMENT_TYPE_NEVER_QUESTION:
        return False
    return _ends_with_question_mark(reply)


def _judgment_type_code(judgment: str) -> str | None:
    if not judgment:
        return None
    first_line = judgment.strip().splitlines()[0] if judgment.strip() else ""
    parts = [part.strip() for part in first_line.split("|")]
    if len(parts) < 2:
        return None
    code = parts[1].upper()
    return code if len(code) == 1 and code.isalpha() else None


def _ends_with_question_mark(reply: str) -> bool:
    stripped = (reply or "").rstrip()
    return bool(stripped) and stripped[-1] in _QUESTION_MARKS
