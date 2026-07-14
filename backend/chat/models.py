# The chat app retains only its NLP services (embedding / emotion / filter),
# shared by the api app. The legacy Conversation / Message models and the
# unauthenticated HumanHumanConsumer (ws/hh/) were removed in favour of the
# api.DialogueMatch + api.MatchMessage matching stack (ws/matching/rooms/).
