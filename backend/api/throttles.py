from rest_framework.throttling import AnonRateThrottle, UserRateThrottle


class GuestLoginRateThrottle(AnonRateThrottle):
    scope = "guest_login"


class DialogueReplyRateThrottle(UserRateThrottle):
    scope = "dialogue_reply"


class DialogueSessionSemanticTreeAnalyzeRateThrottle(UserRateThrottle):
    scope = "dialogue_session_semantic_tree_analyze"


class HistoryConversationSemanticTreeAnalyzeRateThrottle(UserRateThrottle):
    scope = "history_conversation_semantic_tree_analyze"


class MatchingRoomSemanticTreeAnalyzeRateThrottle(UserRateThrottle):
    scope = "matching_room_semantic_tree_analyze"
