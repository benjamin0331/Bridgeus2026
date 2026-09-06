from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.contrib.auth.password_validation import validate_password as dj_validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction

from apps.summary.models import VideoRecommendation, ViewpointNode

from .dialogue_topics import TOPIC_CONFIGS, get_dialogue_survey
from .models import (
    AIConversation,
    DiscomfortReport,
    MatchMessage,
    PlatformDisplaySetting,
    PlatformFeedback,
    PolicyIdea,
    PostDialogueResponse,
)
from .permissions import RESEARCHER_GROUP_NAME

User = get_user_model()


class BridgeUsTokenObtainPairSerializer(TokenObtainPairSerializer):
    """在 JWT access token 的 payload 裡加 is_researcher claim。

    前端 LoginPage 本來就會用 getAccessTokenPayload() 解析 access token 拿
    user_id，這裡多加一個 claim 之後前端不用另外呼叫 API，解 token 就能知道
    目前登入的是不是研究者帳號——用來讓 /viewpoint-review 之類的研究者專用
    頁面知道要不要顯示連結（實際的存取控制仍然是後端 IsResearcher，這裡只是
    給前端一個「要不要顯示」的依據，不是真正的權限判斷）。

    用「研究者」Group 而不是 is_staff：is_staff 語意上是「能不能登入 Django
    /admin/」，跟「有沒有研究者身分」是兩件不一定相同的事。
    """

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token["is_researcher"] = user.groups.filter(name=RESEARCHER_GROUP_NAME).exists()
        return token


class ViewpointNodeReviewSerializer(serializers.ModelSerializer):
    """M6 觀點知識庫 Step 4 人工終審用的唯讀列表/詳情欄位。

    dialogue_id 從 summary 帶出來，方便審核者對照原始對話（前端目前還沒有連到
    原始對話內容的連結，只能先靠 dialogue_id 手動查）。
    """

    dialogue_id = serializers.CharField(source="summary.dialogue_id", read_only=True)
    # summary_id 本身（DialogueSummary 的 PK），跟 dialogue_id（DialogueMatch
    # 的 id，字串形式）是兩回事：前端審核清單用這個欄位把同一場對話產生的
    # 多筆觀點歸類在一起審核，不用另外自己拼字串比對 dialogue_id。
    dialogue_summary_id = serializers.IntegerField(source="summary_id", read_only=True)
    reviewed_by_username = serializers.CharField(
        source="reviewed_by.username", read_only=True, default=None
    )

    class Meta:
        model = ViewpointNode
        fields = [
            "id",
            "dialogue_id",
            "dialogue_summary_id",
            "topic_id",
            "dimension",
            "speaker_side",
            "stance_direction",
            "user_input_text",
            "ai_response_text",
            "viewpoint_summary",
            "composite_score",
            "score_detail",
            "citation_count",
            "review_status",
            "reviewed_by_username",
            "reviewed_at",
            "review_notes",
            "created_at",
        ]
        read_only_fields = fields


class ViewpointNodeReviewDecisionSerializer(serializers.Serializer):
    # reset：把已通過/未通過的節點退回「待審核」，讓它重新走一次審核流程
    # （不是在通過/未通過之間直接切換，是真的送回佇列從頭審）。
    action = serializers.ChoiceField(choices=["approve", "reject", "reset"])
    notes = serializers.CharField(required=False, allow_blank=True, default="")


class ViewpointHighlightSerializer(serializers.Serializer):
    """知識庫首頁「熱門對話」卡片、以及「觀看更多」清單用的公開唯讀欄位——
    只允許已通過人工審核（review_status=approved）的 ViewpointNode 走這條
    序列化，見 views.KnowledgeBaseHighlightsView / KnowledgeBaseViewpointBrowseView。

    帶 user_input_text/ai_response_text 這組原始逐字稿：知識庫「對話詳情」頁
    已經改成顯示完整逐字稿 + CCND 樹（KnowledgeBaseConversationDetailView），
    這裡只是把同一個隱私範圍決定延伸到小卡本身——只有走過人工審核通過的
    節點才會用這條序列化，一般使用者只看得到「已核准可公開」的內容。"""

    id = serializers.IntegerField()
    dialogue_summary_id = serializers.IntegerField()
    topic_id = serializers.IntegerField()
    topic_title = serializers.CharField()
    dimension = serializers.CharField()
    dimension_name = serializers.CharField()
    speaker_side = serializers.CharField(allow_blank=True)
    stance_direction = serializers.CharField(allow_blank=True)
    viewpoint_summary = serializers.CharField(allow_blank=True)
    user_input_text = serializers.CharField(allow_blank=True)
    ai_response_text = serializers.CharField(allow_blank=True)
    citation_count = serializers.IntegerField()
    composite_score = serializers.FloatField(allow_null=True)
    created_at = serializers.DateTimeField()


class ApprovedDialogueMessageSerializer(serializers.Serializer):
    """知識庫對話詳情頁的逐字稿——只標示 A/B 方，不帶 sender_id/使用者名稱，
    維持跟即時聊天室同一套匿名精神：這裡任何登入使用者都能看，但看到的仍然
    是「A 方說了什麼」而不是「誰說的」。
    """

    id = serializers.CharField()
    side = serializers.CharField()
    sender_label = serializers.CharField()
    content = serializers.CharField()
    created_at = serializers.DateTimeField()


class DialogueSummaryDetailSerializer(serializers.Serializer):
    """知識庫「熱門對話」卡片點進去的對話紀錄。除了 DialogueSummary 已沉澱的
    摘要欄位，也回傳完整逐字稿（messages，僅標示 A/B 方）跟雙方的 CCND 語意樹
    （semantic_tree），讓前端可以用跟聊天室一致的「訊息串 + CCND 樹狀圖」呈現，
    不再只是精簡摘要卡片。逐字稿不帶 sender_id/使用者名稱，維持匿名。"""

    dialogue_summary_id = serializers.IntegerField()
    topic_id = serializers.IntegerField()
    topic_title = serializers.CharField()
    summary_text = serializers.CharField(allow_blank=True)
    side_a_stance = serializers.CharField(allow_blank=True)
    side_b_stance = serializers.CharField(allow_blank=True)
    quality_score = serializers.FloatField(allow_null=True)
    stance_shift_magnitude = serializers.FloatField(allow_null=True)
    created_at = serializers.DateTimeField()
    viewpoints = ViewpointHighlightSerializer(many=True)
    messages = ApprovedDialogueMessageSerializer(many=True)
    # 用 DictField 而不是 MatchingRoomSemanticTreeSerializer：那個類別定義在
    # 這個檔案更後面，這裡直接參照會在 import 當下 NameError；反正這裡只做
    # 序列化輸出（不需要驗證輸入），值也已經是 approved_match_tree_payload()
    # 組好的正確結構，DictField 原樣透傳即可。
    semantic_tree = serializers.DictField()


class VideoRecommendationSerializer(serializers.ModelSerializer):
    class Meta:
        model = VideoRecommendation
        fields = [
            "id",
            "title",
            "url",
            "thumbnail_url",
            "description",
            "topic_id",
            "created_at",
        ]


class VideoRecommendationAdminSerializer(serializers.ModelSerializer):
    """研究者專用：知識庫影片管理面板（前端設定頁）用，比公開的
    VideoRecommendationSerializer 多帶 is_published/display_order——這兩個
    欄位控制影片會不會出現在知識庫首頁、排序順序，只有研究者需要看/改。

    video_file 是研究者從設定頁本地上傳的影片檔；url 改成非必填——上傳
    video_file 時由 VideoRecommendationAdminListCreateView.perform_create()
    自動從檔案路徑補上 url，呼叫端不用自己算。兩者都沒帶才會擋在
    validate()（總要有個能播放的來源）。"""

    class Meta:
        model = VideoRecommendation
        fields = [
            "id",
            "title",
            "url",
            "video_file",
            "thumbnail_url",
            "description",
            "topic_id",
            "stance_direction",
            "is_published",
            "display_order",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]
        extra_kwargs = {"url": {"required": False}}

    def validate(self, attrs):
        has_url = bool(attrs.get("url") or getattr(self.instance, "url", ""))
        has_file = bool(attrs.get("video_file") or getattr(self.instance, "video_file", None))
        if not has_url and not has_file:
            raise serializers.ValidationError("請上傳影片檔案，或填寫影片網址。")
        return attrs


class AIConversationSerializer(serializers.ModelSerializer):
    class Meta:
        model = AIConversation
        fields = '__all__'


class MessageReactionSerializer(serializers.Serializer):
    """讚/倒讚 on an opponent's message. value 0 = remove the reaction."""

    target_type = serializers.ChoiceField(choices=["ai", "match"])
    target_id = serializers.IntegerField(min_value=1)
    value = serializers.ChoiceField(choices=[1, -1, 0])


class FavoriteToggleSerializer(serializers.Serializer):
    """POST /api/favorites/ 的輸入。切換語意：已收藏就取消，未收藏就收藏，
    所以不收 value/active 之類的欄位——由後端讀目前狀態決定，前端連按兩次
    也不會因為送出的狀態過期而卡在錯的一邊。"""

    target_type = serializers.ChoiceField(choices=["viewpoint", "video"])
    target_id = serializers.IntegerField(min_value=1)


class DialogueSessionCreateSerializer(serializers.Serializer):
    topic_id = serializers.IntegerField()
    topic_title = serializers.CharField(max_length=255)
    topic_description = serializers.CharField(
        max_length=500,
        required=False,
        allow_blank=True,
    )
    survey_answers = serializers.DictField(
        child=serializers.IntegerField(min_value=1, max_value=7),
        required=False,
    )
    survey_open_answers = serializers.DictField(
        child=serializers.CharField(
            allow_blank=True,
            trim_whitespace=False,
            max_length=2000,
        ),
        required=False,
    )
    user_initial_argument = serializers.CharField(required=False, allow_blank=True)


class DialogueReplySerializer(serializers.Serializer):
    message = serializers.CharField(max_length=4000)


class DialogueTopicSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField(max_length=255)
    description = serializers.CharField(max_length=500)
    date = serializers.CharField(max_length=20)


class DialogueTopicTrendingSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField(max_length=255)
    hits = serializers.IntegerField()


class PolicyIdeaSerializer(serializers.ModelSerializer):
    """join.gov.tw 提案的對外形狀。

    刻意不吐 outline：目前唯一的消費者是 Godot 教學青蛙的對話框，一句台詞塞得下
    的只有標題跟附議數，outline 動輒兩三百字。要用的時候再開，不要為了「有總比
    沒有好」先塞進 payload——那是每次請求都要付的成本。
    """

    class Meta:
        model = PolicyIdea
        fields = [
            "external_id",
            "section",
            "rank",
            "title",
            "url",
            "endorse_count",
            "endorse_goal",
            "categories",
            "organizations",
            "publish_date",
            "fetched_at",
        ]


class DialogueSurveyQuestionSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    code = serializers.CharField(max_length=10, required=False)
    tag = serializers.CharField(max_length=100)
    text = serializers.CharField(max_length=500)
    question_type = serializers.CharField(max_length=30, required=False)
    dimension = serializers.CharField(max_length=50, required=False, allow_blank=True)
    direction = serializers.CharField(max_length=20, required=False, allow_blank=True)
    reverse_scored = serializers.BooleanField(required=False)
    min_value = serializers.IntegerField(required=False)
    max_value = serializers.IntegerField(required=False)
    placeholder = serializers.CharField(
        max_length=500,
        required=False,
        allow_blank=True,
    )
    min_sentences = serializers.IntegerField(required=False)
    max_sentences = serializers.IntegerField(required=False)


class DialogueSurveySerializer(serializers.Serializer):
    topic_id = serializers.IntegerField()
    title = serializers.CharField(max_length=255)
    subtitle = serializers.CharField(max_length=255)
    scale = serializers.DictField(required=False)
    stance_rules = serializers.DictField(required=False)
    questions = DialogueSurveyQuestionSerializer(many=True)
    open_questions = DialogueSurveyQuestionSerializer(many=True, required=False)
    semantic_vector_interface = serializers.DictField(required=False)


class MatchingJoinSerializer(serializers.Serializer):
    topic_id = serializers.IntegerField()
    survey_answers = serializers.DictField(
        child=serializers.IntegerField(min_value=1, max_value=7),
    )
    survey_open_answers = serializers.DictField(
        child=serializers.CharField(
            allow_blank=True,
            trim_whitespace=False,
            max_length=2000,
        ),
        required=False,
    )
    restart_existing_match = serializers.BooleanField(
        default=False,
        required=False,
    )

    def validate(self, attrs):
        topic_id = attrs["topic_id"]
        survey = get_dialogue_survey(topic_id)
        if not survey:
            raise serializers.ValidationError(
                {"topic_id": "找不到這個議題的問卷設定。"}
            )

        required_question_ids = {
            str(question["id"]) for question in survey.get("questions", [])
        }
        provided_question_ids = set(attrs["survey_answers"].keys())
        missing_question_ids = sorted(required_question_ids - provided_question_ids)
        if missing_question_ids:
            raise serializers.ValidationError(
                {
                    "survey_answers": (
                        "量表題尚未填寫完整，缺少題號："
                        + ", ".join(missing_question_ids)
                    )
                }
            )

        return attrs


class MatchingTopicSerializer(serializers.Serializer):
    topic_id = serializers.IntegerField()

    def validate_topic_id(self, value):
        if not get_dialogue_survey(value):
            raise serializers.ValidationError("找不到這個議題的問卷設定。")
        return value


class MatchingStateSerializer(serializers.Serializer):
    topic_id = serializers.IntegerField()
    status = serializers.CharField(max_length=20)
    stance_score = serializers.DecimalField(
        max_digits=4,
        decimal_places=2,
        required=False,
        allow_null=True,
    )
    stance_category = serializers.CharField(
        max_length=20,
        required=False,
        allow_null=True,
    )
    queue_entry_id = serializers.IntegerField(required=False, allow_null=True)
    waiting_started_at = serializers.DateTimeField(required=False, allow_null=True)
    matched_at = serializers.DateTimeField(required=False, allow_null=True)
    cancelled_at = serializers.DateTimeField(required=False, allow_null=True)
    closed_at = serializers.DateTimeField(required=False, allow_null=True)
    match_id = serializers.IntegerField(required=False, allow_null=True)
    room_id = serializers.CharField(max_length=64, required=False, allow_null=True)
    other_user_id = serializers.IntegerField(required=False, allow_null=True)
    other_user_name = serializers.CharField(max_length=150, required=False, allow_null=True)
    presence = serializers.JSONField(required=False, allow_null=True)
    absence_deadline = serializers.DateTimeField(required=False, allow_null=True)
    fallback_offer = serializers.JSONField(required=False, allow_null=True)
    binding_source = serializers.CharField(
        max_length=16, required=False, allow_null=True
    )
    survey_required = serializers.BooleanField(required=False)
    survey_deadline = serializers.DateTimeField(required=False, allow_null=True)
    partner_state = serializers.CharField(
        max_length=16, required=False, allow_null=True
    )
    binding_cancel_reason = serializers.CharField(
        max_length=32, required=False, allow_null=True
    )


class GodotSurveySerializer(serializers.Serializer):
    """Godot 綁定房的前測問卷。不含 restart_existing_match——這條路徑不排隊。"""

    topic_id = serializers.IntegerField()
    survey_answers = serializers.DictField(child=serializers.IntegerField())
    survey_open_answers = serializers.DictField(
        child=serializers.CharField(allow_blank=True), required=False, default=dict
    )


class MatchMessageSerializer(serializers.ModelSerializer):
    """sender_name 從序列化情境的 context["anon_ids"] 查表取得（見
    views._build_room_messages_payload），不在這裡重查 obj.match 避免多一趟
    查詢——呼叫端已經有 match 物件、算過一次 anon_ids 就直接傳進來共用。"""

    sender_id = serializers.IntegerField(source="sender.id", read_only=True)
    sender_name = serializers.SerializerMethodField()

    def get_sender_name(self, obj):
        anon_ids = self.context.get("anon_ids") or {}
        return anon_ids.get(obj.sender_id, "匿名使用者")

    class Meta:
        model = MatchMessage
        fields = (
            "id",
            "sender_id",
            "sender_name",
            "content",
            "created_at",
        )


class MatchingRoomMessageCreateSerializer(serializers.Serializer):
    content = serializers.CharField(max_length=4000, trim_whitespace=False)

    def validate_content(self, value):
        if not value.strip():
            raise serializers.ValidationError("訊息內容不可為空白。")
        return value


class MatchingRoomStanceDriftSerializer(serializers.Serializer):
    drift_value = serializers.FloatField()
    measured_at = serializers.DateTimeField()


class MatchingRoomOpeningSerializer(serializers.Serializer):
    """H-H 的 AI 開場卡片（一房共用一則）。"""

    content = serializers.CharField()
    directions = serializers.JSONField()
    source = serializers.CharField(max_length=16)
    created_at = serializers.CharField(max_length=64)


class MatchingRoomMessagesSerializer(serializers.Serializer):
    room_id = serializers.CharField(max_length=64)
    match_id = serializers.IntegerField()
    topic_id = serializers.IntegerField()
    status = serializers.CharField(max_length=20)
    other_user_id = serializers.IntegerField(required=False, allow_null=True)
    other_user_name = serializers.CharField(max_length=150, required=False, allow_null=True)
    stance_drift = MatchingRoomStanceDriftSerializer(required=False, allow_null=True)
    opening = MatchingRoomOpeningSerializer(required=False, allow_null=True)
    presence = serializers.JSONField(required=False, allow_null=True)
    absence_deadline = serializers.DateTimeField(required=False, allow_null=True)
    messages = MatchMessageSerializer(many=True)


class MatchingRoomSemanticTreeTimelineSerializer(serializers.Serializer):
    # room_id is always present — for AI sessions it's an alias of session_id
    # (there's no real "room"), so both conversation kinds can share this one
    # serializer/shape instead of branching by kind on the frontend.
    room_id = serializers.CharField(max_length=64)
    match_id = serializers.IntegerField(required=False, allow_null=True)
    session_id = serializers.CharField(max_length=64, required=False)
    topic_id = serializers.IntegerField(allow_null=True)
    asOfMessageId = serializers.CharField(max_length=64)
    asOfTimestamp = serializers.CharField(max_length=64)
    treeData = serializers.JSONField()
    anchors = serializers.JSONField()
    # Nodes this specific message created ([{id, name, stance}]), so the slider
    # can say "這則新增了哪些節點" instead of leaving the diff purely visual.
    bornNodes = serializers.JSONField(required=False)


class MatchingRoomSemanticTreeSerializer(serializers.Serializer):
    room_id = serializers.CharField(max_length=64, required=False)
    match_id = serializers.IntegerField(required=False)
    session_id = serializers.CharField(max_length=64, required=False)
    topic_id = serializers.IntegerField(required=False, allow_null=True)
    semanticMode = serializers.CharField(max_length=32, required=False)
    treeData = serializers.JSONField()
    trees = serializers.JSONField()
    anchors = serializers.JSONField()
    analysisHistory = serializers.JSONField()
    analyzedMessageIds = serializers.ListField(child=serializers.CharField())
    analyzedSourceIds = serializers.ListField(child=serializers.CharField(), required=False)
    analysisStatus = serializers.CharField(max_length=32)
    message = serializers.CharField(required=False, allow_blank=True)
    analyzedCount = serializers.IntegerField()


def _likert_7(required=True):
    return serializers.IntegerField(min_value=1, max_value=7, required=required)


class PostDialogueResponseSerializer(serializers.Serializer):
    # Meta
    topic_id = serializers.IntegerField()
    session_id = serializers.CharField(max_length=64, required=False, allow_blank=True, allow_null=True)
    room_id = serializers.CharField(max_length=64, required=False, allow_blank=True, allow_null=True)
    experiment_condition = serializers.ChoiceField(choices=["ai", "hh"])

    # C-1 (8 Likert, scrambled order; reverse scoring handled in model)
    post_likert_1 = _likert_7()
    post_likert_2 = _likert_7()
    post_likert_3 = _likert_7()
    post_likert_4 = _likert_7()
    post_likert_5 = _likert_7()
    post_likert_6 = _likert_7()
    post_likert_7 = _likert_7()
    post_likert_8 = _likert_7()

    # C-2 (7 Likert)
    exp_stance_change_1 = _likert_7()
    exp_stance_change_2 = _likert_7()
    exp_quality_1 = _likert_7()
    exp_quality_2 = _likert_7()
    exp_reflection_1 = _likert_7()
    exp_reflection_2 = _likert_7()
    exp_comprehension_1 = _likert_7()

    # C-3 (3 Likert)
    ccnd_attention = _likert_7()
    ccnd_awareness = _likert_7()
    ccnd_influence = _likert_7()

    # C-4 (H-AI only; H-H sends null)
    opponent_judgment = serializers.ChoiceField(
        choices=[1, 2, 3],
        required=False,
        allow_null=True,
    )

    # D
    post_open_comprehension = serializers.CharField(
        min_length=30,
        max_length=4000,
        trim_whitespace=False,
        error_messages={"min_length": "D1 最少需填寫 30 字。"},
    )
    post_open_feedback = serializers.CharField(
        max_length=4000,
        required=False,
        allow_blank=True,
        trim_whitespace=False,
    )

    # E
    discomfort_flag = serializers.BooleanField(default=False)
    discomfort_detail = serializers.CharField(
        max_length=2000,
        required=False,
        allow_blank=True,
        trim_whitespace=False,
    )

    def validate(self, attrs):
        condition = attrs.get("experiment_condition")
        judgment = attrs.get("opponent_judgment")

        if condition == "ai" and judgment is None:
            raise serializers.ValidationError(
                {"opponent_judgment": "H-AI 組必須填寫對話對象判斷（C4）。"}
            )
        if condition == "hh" and judgment is not None:
            raise serializers.ValidationError(
                {"opponent_judgment": "H-H 組不填此題，請傳入 null。"}
            )

        if attrs.get("discomfort_flag") and not attrs.get("discomfort_detail", "").strip():
            raise serializers.ValidationError(
                {"discomfort_detail": "選擇「是」時請描述不適情況。"}
            )

        session_id = attrs.get("session_id")
        room_id = attrs.get("room_id")
        if condition == "ai" and (not session_id or room_id):
            raise serializers.ValidationError(
                {
                    "session_id": "H-AI 組必須只提供 session_id。",
                    "room_id": "H-AI 組不可提供 room_id。",
                }
            )
        if condition == "hh" and (not room_id or session_id):
            raise serializers.ValidationError(
                {
                    "room_id": "H-H 組必須只提供 room_id。",
                    "session_id": "H-H 組不可提供 session_id。",
                }
            )

        return attrs


class PostDialogueResponseConsentSerializer(serializers.Serializer):
    consent_confirmed = serializers.BooleanField()


class PlatformFeedbackSerializer(serializers.Serializer):
    """Part F — platform experience feedback (7 items)."""

    response_id = serializers.IntegerField()

    # F1–F5 satisfaction Likert (1–7)
    ux_matching = _likert_7()
    ux_chatroom = _likert_7()
    ux_nlp_intervention = _likert_7()
    ux_ccnd = _likert_7()
    ux_overall = _likert_7()

    # F6 NPS (0–10)
    nps_score = serializers.IntegerField(min_value=0, max_value=10)

    # F7 open-ended (optional)
    ux_improvement = serializers.CharField(
        max_length=4000,
        required=False,
        allow_blank=True,
        allow_null=True,
        trim_whitespace=False,
    )


class PlatformFeedbackOutputSerializer(serializers.ModelSerializer):
    mean_ux = serializers.SerializerMethodField()
    nps_category = serializers.SerializerMethodField()

    def get_mean_ux(self, obj):
        return obj.mean_ux()

    def get_nps_category(self, obj):
        return obj.nps_category()

    class Meta:
        model = PlatformFeedback
        fields = [
            "id",
            "response",
            "ux_matching", "ux_chatroom", "ux_nlp_intervention",
            "ux_ccnd", "ux_overall",
            "nps_score", "ux_improvement",
            "mean_ux", "nps_category",
            "created_at",
        ]
        read_only_fields = fields


class PostDialogueResponseOutputSerializer(serializers.ModelSerializer):
    s_post = serializers.SerializerMethodField()
    # Derived stance metrics (aliased from the stored *_value snapshot columns)
    delta_s = serializers.FloatField(source="delta_s_value", read_only=True)
    stance_centrism = serializers.FloatField(
        source="stance_centrism_value",
        read_only=True,
    )
    pre_question_map = serializers.SerializerMethodField()
    # 結算畫面要顯示議題名而不是 topic_id；標題只存在 TOPIC_CONFIGS，沒有
    # 對應的資料表，也不受 TopicDisplayOverride 覆寫（那層只管可見性與門檻）。
    topic_title = serializers.SerializerMethodField()
    substantive_turn_count = serializers.SerializerMethodField()
    lit_anchor_count = serializers.SerializerMethodField()

    def get_s_post(self, obj):
        return obj.s_post()

    def get_topic_title(self, obj):
        return TOPIC_CONFIGS.get(obj.topic_id, {}).get("title", "")

    def get_substantive_turn_count(self, obj):
        """這場對話的實質發言輪數，給結算畫面的「參與度」軸用。

        欄位本來就存在，只是分在兩張表：H-AI 在 DialogueSessionRecord、H-H 在
        MatchInputGateStat（per match+user）。兩者都在後測送出時才被
        input_gate_store 填上（見該模組），而結算畫面正好是後測送出後才顯示，
        所以這裡讀到的一定是最終值。查不到就回 None，前端會把那一軸留空。
        """
        from .models import DialogueSessionRecord, MatchInputGateStat

        if obj.session_id:
            record = (
                DialogueSessionRecord.objects
                .filter(session_id=obj.session_id)
                .only("substantive_turn_count")
                .first()
            )
            if record is not None:
                return record.substantive_turn_count

        if obj.room_id:
            stat = (
                MatchInputGateStat.objects
                .filter(match__room_id=obj.room_id, user_id=obj.user_id)
                .only("substantive_turn_count")
                .first()
            )
            if stat is not None:
                return stat.substantive_turn_count

        return None

    def get_lit_anchor_count(self, obj):
        """點亮的 CCND depth-1 大分類 anchor 數（0–6），給結算畫面的「廣度」軸用。

        同 substantive_turn_count 的處理：欄位分在兩張表（H-AI 在
        DialogueSessionRecord、H-H 在 MatchInputGateStat per match+user），
        都在後測送出時由 input_gate_store 落庫，這裡只讀不即時算。查不到回
        None，前端把那一軸留空、不計入級距平均。
        """
        from .models import DialogueSessionRecord, MatchInputGateStat

        if obj.session_id:
            record = (
                DialogueSessionRecord.objects
                .filter(session_id=obj.session_id)
                .only("lit_anchor_count")
                .first()
            )
            if record is not None:
                return record.lit_anchor_count

        if obj.room_id:
            stat = (
                MatchInputGateStat.objects
                .filter(match__room_id=obj.room_id, user_id=obj.user_id)
                .only("lit_anchor_count")
                .first()
            )
            if stat is not None:
                return stat.lit_anchor_count

        return None

    def get_pre_question_map(self, _obj):
        from .models import POST_LIKERT_TO_PRE_QUESTION
        return POST_LIKERT_TO_PRE_QUESTION

    class Meta:
        model = PostDialogueResponse
        fields = [
            "id", "topic_id", "topic_title", "experiment_condition",
            "session_id", "room_id",
            "post_likert_1", "post_likert_2", "post_likert_3", "post_likert_4",
            "post_likert_5", "post_likert_6", "post_likert_7", "post_likert_8",
            "exp_stance_change_1", "exp_stance_change_2",
            "exp_quality_1", "exp_quality_2",
            "exp_reflection_1", "exp_reflection_2",
            "ccnd_attention", "ccnd_awareness", "ccnd_influence",
            "opponent_judgment",
            "post_open_comprehension", "post_open_feedback",
            "discomfort_flag", "consent_confirmed",
            "s_pre", "s_post", "delta_s", "stance_centrism",
            "substantive_turn_count",
            "lit_anchor_count",
            "pre_question_map",
            "created_at",
        ]
        read_only_fields = fields


class AccountListSerializer(serializers.ModelSerializer):
    """帳號管理清單／回傳用（唯讀）。is_researcher 由「研究者」Group 推導。"""

    is_researcher = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "is_active",
            "is_researcher",
            "is_superuser",
            "last_login",
            "date_joined",
        ]
        read_only_fields = fields

    def get_is_researcher(self, obj):
        return any(g.name == RESEARCHER_GROUP_NAME for g in obj.groups.all())


class AccountCreateSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150, validators=[UnicodeUsernameValidator()])
    password = serializers.CharField(write_only=True)
    is_researcher = serializers.BooleanField(required=False, default=False)

    def validate_username(self, value):
        # iexact 而非精確比對：Django 的登入是大小寫敏感的，若允許 Alice 與
        # alice 並存，使用者會穩定產生「我明明註冊過卻登不進去」的困惑。
        # 這裡擋掉只差大小寫的重複，登入本身維持原本的精確比對語意。
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("這個帳號名稱已經有人用了。")
        return value

    def validate(self, attrs):
        # 密碼驗證放在 validate() 而不是 validate_password()：
        # UserAttributeSimilarityValidator 在 user=None 時會直接 return，
        # 而單欄位的 validate_password() 拿不到 username。少了這個 user 參數，
        # settings.AUTH_PASSWORD_VALIDATORS 的第一個 validator 等於從未生效
        # ——密碼可以直接等於帳號名稱。
        try:
            dj_validate_password(
                attrs["password"], user=User(username=attrs["username"])
            )
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"password": list(exc.messages)})
        return attrs

    def create(self, validated_data):
        is_researcher = validated_data.pop("is_researcher", False)
        try:
            # savepoint：Postgres 在 IntegrityError 之後會讓當前交易進入
            # aborted 狀態，沒有 atomic 包住的話，後續任何查詢都會拋
            # TransactionManagementError 而不是我們想回的 400。
            with transaction.atomic():
                user = User.objects.create_user(
                    username=validated_data["username"],
                    password=validated_data["password"],
                )
        except IntegrityError:
            # validate_username 的 exists() 與這裡之間有空窗。研究者手動開帳號
            # 撞不到；公開註冊兩人同時送出相同 username 就會，而未捕捉的
            # IntegrityError 會變成 500 而不是 400。
            raise serializers.ValidationError(
                {"username": ["這個帳號名稱已經有人用了。"]}
            )
        if is_researcher:
            group, _ = Group.objects.get_or_create(name=RESEARCHER_GROUP_NAME)
            user.groups.add(group)  # signal 會把 is_staff 設成 True
        return user


class AccountUpdateSerializer(serializers.Serializer):
    is_active = serializers.BooleanField(required=False)
    is_researcher = serializers.BooleanField(required=False)


class PasswordResetSerializer(serializers.Serializer):
    """重設某帳號的密碼。

    呼叫端必須用 context 傳入 target（被重設的那個 User），
    UserAttributeSimilarityValidator 才比對得到「密碼與帳號名稱太像」。
    見 AccountPasswordResetView。
    """

    password = serializers.CharField(write_only=True)

    def validate_password(self, value):
        try:
            dj_validate_password(value, user=self.context.get("target"))
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages))
        return value


class MeProfileUpdateSerializer(serializers.Serializer):
    """使用者自己改自己的顯示名稱與 email（PATCH /api/me/）。

    只列出可改的兩個欄位，其餘一律不理會——用白名單而不是 exclude：
    username 是登入帳號也是研究資料的對應鍵，is_researcher / is_staff /
    is_research_subject 更是「讓受試者自己填自己的實驗紀錄」那一類，
    未來 User 加欄位時也不該預設變成可自助修改。

    呼叫端必須用 context 傳入 user：email 的唯一性要排除自己，否則使用者
    把原本的 email 原樣送回來會被自己擋下。
    """

    display_name = serializers.CharField(
        max_length=50, required=False, allow_blank=True
    )
    # allow_blank=False：清空 email 等於把未來的信件重設管道關掉，而使用者
    # 多半是誤刪。要換信箱就填新的；真的要拿掉請研究者處理。
    email = serializers.EmailField(required=False, allow_blank=False)
    # 新手導覽看完了沒。不是「個人資料」但同屬「只關於自己、改壞了也只影響
    # 自己」那一類，塞在這裡比為了一個布林值再開一支 endpoint 划算。
    # False 會把時間戳清掉，等於讓導覽下次登入再自動跳一次。
    onboarding_completed = serializers.BooleanField(required=False)

    def validate_email(self, value):
        # iexact 而非精確比對，與註冊（accounts/serializers.py）同一套規則。
        clash = User.objects.filter(email__iexact=value).exclude(
            pk=self.context["user"].pk
        )
        if clash.exists():
            raise serializers.ValidationError("這個 email 已經註冊過了。")
        return value


class ChangePasswordSerializer(serializers.Serializer):
    """使用者自己改自己的密碼。

    與 PasswordResetSerializer（研究者重設別人的）刻意分開：這裡多一道
    old_password 驗證，而研究者本來就不知道對方的舊密碼。

    呼叫端必須用 context 傳入 user，理由同 PasswordResetSerializer：
    UserAttributeSimilarityValidator 拿不到 user 就會直接 return，等於
    settings.AUTH_PASSWORD_VALIDATORS 的第一個 validator 從未生效。
    """

    old_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True)
    new_password_confirm = serializers.CharField(write_only=True)

    def validate_old_password(self, value):
        user = self.context["user"]
        if not user.check_password(value):
            raise serializers.ValidationError("目前的密碼不正確。")
        return value

    def validate(self, attrs):
        if attrs["new_password"] != attrs["new_password_confirm"]:
            raise serializers.ValidationError(
                {"new_password_confirm": "兩次輸入的新密碼不一致。"}
            )

        # 新舊相同就拒絕：使用者會以為自己換過了，實際上外流的那組仍然有效。
        if attrs["new_password"] == attrs["old_password"]:
            raise serializers.ValidationError(
                {"new_password": "新密碼不能與目前的密碼相同。"}
            )

        try:
            dj_validate_password(attrs["new_password"], user=self.context["user"])
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"new_password": list(exc.messages)})
        return attrs


class PlatformDisplaySettingSerializer(serializers.ModelSerializer):
    class Meta:
        model = PlatformDisplaySetting
        fields = [
            "participant_entry_mode",
            "researcher_entry_mode",
            "match_fallback_timeout_minutes",
            "updated_at",
        ]
        read_only_fields = ["updated_at"]

    def validate_match_fallback_timeout_minutes(self, value):
        if not 1 <= value <= 120:
            raise serializers.ValidationError("等待時間需介於 1 到 120 分鐘。")
        return value


class TopicDisplayOverrideSerializer(serializers.Serializer):
    """單一議題的顯示覆寫。四個欄位都選填；門檻傳 null＝還原成程式碼預設值。

    門檻驗證必須看「套用後的實際結果」而不是只看這次送來的欄位：只送
    support=3.0 但目前 oppose 是 3.5 的話，合起來是不合法的，得擋下來。
    """

    visible_to_participant = serializers.BooleanField(required=False)
    visible_to_researcher = serializers.BooleanField(required=False)
    support_threshold = serializers.FloatField(required=False, allow_null=True)
    oppose_threshold = serializers.FloatField(required=False, allow_null=True)

    def validate(self, attrs):
        from .display_settings import default_stance_thresholds, get_stance_thresholds

        topic_id = self.context["topic_id"]
        survey_config = get_dialogue_survey(topic_id) or {}
        scale_config = survey_config.get("scale", {})
        scale_min = float(scale_config.get("min", 1))
        scale_max = float(scale_config.get("max", 7))

        current_support, current_oppose = get_stance_thresholds(topic_id=topic_id)
        default_support, default_oppose = default_stance_thresholds(topic_id=topic_id)

        def resolve(field, current, default):
            if field not in attrs:
                return current
            value = attrs[field]
            return default if value is None else float(value)

        support = resolve("support_threshold", current_support, default_support)
        oppose = resolve("oppose_threshold", current_oppose, default_oppose)

        for label, value in (("支持門檻", support), ("反對門檻", oppose)):
            if not scale_min <= value <= scale_max:
                raise serializers.ValidationError(
                    f"{label}需介於 {scale_min} 到 {scale_max} 之間。"
                )

        if oppose >= support:
            raise serializers.ValidationError("反對門檻必須小於支持門檻。")

        return attrs


class DialogueEntrySerializer(serializers.Serializer):
    """混合入口的輸入。

    刻意不收 topic_title / topic_description / user_initial_argument——
    那些一律由後端從 TOPIC_CONFIGS 與問卷 Q9 補齊，少一組可被客戶端
    竄改的輸入。
    """

    topic_id = serializers.IntegerField()
    survey_answers = serializers.DictField(
        child=serializers.IntegerField(min_value=1, max_value=7),
    )
    survey_open_answers = serializers.DictField(
        child=serializers.CharField(
            allow_blank=True,
            trim_whitespace=False,
            max_length=2000,
        ),
        required=False,
    )
