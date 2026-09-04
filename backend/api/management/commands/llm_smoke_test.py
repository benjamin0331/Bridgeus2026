"""演示前必跑：實際打一次 API，確認目前的 LLM_PROVIDER 撐得住輸出契約。

備案（LLM_PROVIDER=openai）最大的未知數不是「能不能連上」，而是「模型會不會
照系統 prompt 第十節輸出 <judgment>…</judgment><reply>…</reply>」。契約沒過
的代價是多打一次 API 重試，還不過就 salvage，最糟顯示「系統忙碌中」——這種事
只能事前量，不能演示當天才發現。

    # 現行版本（Claude）
    uv run python manage.py llm_smoke_test

    # 備案
    LLM_PROVIDER=openai uv run python manage.py llm_smoke_test

    # 多跑幾輪、換議題
    LLM_PROVIDER=openai uv run python manage.py llm_smoke_test --turns 5 --topic-id 103

會真的花錢（每輪一次 API 呼叫），但只跑指定輪數，不寫任何資料庫。
"""

import asyncio
import time

from django.core.management.base import BaseCommand, CommandError

# 依議題準備的使用者發言。刻意混合：一般論述、短提問、以及一句情緒比較強的，
# 讓契約在不同輸入形狀下都被試過。
_USER_TURNS = [
    "我覺得核電是目前最務實的選擇，你怎麼看？",
    "那核廢料要放哪裡？",
    "你講的那些風險，其實機率非常低吧。",
    "所以你到底支持還是反對？",
    "我還是覺得缺電比較可怕。",
]


class Command(BaseCommand):
    help = "對目前的 LLM_PROVIDER 實際跑幾輪對話，檢查輸出契約遵守率。"

    def add_arguments(self, parser):
        parser.add_argument("--turns", type=int, default=3, help="要跑幾輪（預設 3）")
        parser.add_argument("--topic-id", type=int, default=102, help="議題 ID（預設 102 核能）")
        parser.add_argument(
            "--show-reply",
            action="store_true",
            help="印出每輪的可見回覆全文（預設只印前 80 字）",
        )

    def handle(self, *args, **options):
        from api.dialogue_topics import TOPIC_CONFIGS
        from apps.matching.services.ai_agent import DialogueAgent, DialogueSession
        from core.llm_provider import active_model_name, active_provider

        topic_id = options["topic_id"]
        topic_meta = TOPIC_CONFIGS.get(topic_id)
        if not topic_meta:
            raise CommandError(f"找不到議題 {topic_id}。")

        turns = max(1, min(options["turns"], len(_USER_TURNS)))
        provider = active_provider()
        model = active_model_name()

        self.stdout.write(f"provider = {provider}")
        self.stdout.write(f"model    = {model}")
        self.stdout.write(f"topic    = {topic_id} {topic_meta.get('title', '')}")
        self.stdout.write(f"turns    = {turns}\n")

        # 代理人立場刻意寫死成「反方」：這支指令量的是契約遵守率，不是配對
        # 演算法，用固定立場才能在不同 provider 之間互相比較。
        session = DialogueSession(
            topic=topic_meta.get("title", ""),
            topic_description=topic_meta.get("topic_description", ""),
            agent_stance="較反對核電",
            agent_stance_summary="以反方角度提出核安與核廢料疑慮。",
            user_stance_label="較支持核電",
            user_stance_score=6.0,
            user_initial_argument="核電是兼顧減碳與穩定供電的務實選擇。",
        )
        agent = DialogueAgent(collection_name=topic_meta["collection_name"])

        # 整段跑在同一個 event loop 裡。每輪各開一次 asyncio.run 會讓 httpx 的
        # 連線池跨 loop 被回收，噴一堆 "generator didn't stop after athrow()"
        # ——那是指令的問題，不是備案路徑的問題，但噪音會蓋掉真正的錯誤。
        # 正式環境（Channels）本來就是一個長命 loop，這樣也更接近真實情況。
        passed, failures = asyncio.run(self._run_turns(agent, session, turns, options))

        self.stdout.write("")
        rate = passed / turns * 100
        summary = f"契約一次過：{passed}/{turns}（{rate:.0f}%）  provider={provider} model={model}"
        if passed == turns:
            self.stdout.write(self.style.SUCCESS(summary))
        else:
            self.stdout.write(self.style.WARNING(summary))
            for turn_no, reason in failures:
                self.stdout.write(f"  第 {turn_no} 輪：{reason}")
            self.stdout.write(
                "\n違約率高代表這個 provider 每輪平均要多打一次 API（成本翻倍），"
                "演示前值得調整系統 prompt 第十節或換模型。"
            )

    async def _run_turns(self, agent, session, turns, options):
        from apps.matching.services.ai_agent import DialoguePhase, ReplyStreamGate

        passed = 0
        failures = []
        for index in range(turns):
            user_message = _USER_TURNS[index]
            session.add_user_message(user_message)
            session.dialogue_phase = DialoguePhase.from_turn_count(session.turn_count)

            started = time.monotonic()
            try:
                raw = await _collect(agent, session)
            except Exception as exc:
                failures.append((index + 1, f"API 呼叫失敗：{exc}"))
                self.stdout.write(self.style.ERROR(f"[{index + 1}] API 失敗：{exc}"))
                continue
            elapsed = time.monotonic() - started

            gate = ReplyStreamGate()
            gate.feed(raw)
            _, contract_ok = gate.finish()

            if contract_ok:
                passed += 1
                reply = gate.reply
                shown = reply if options["show_reply"] else reply[:80]
                self.stdout.write(
                    self.style.SUCCESS(f"[{index + 1}] 契約 OK  {elapsed:.1f}s  {shown}")
                )
                session.add_agent_message(reply)
            else:
                failures.append((index + 1, f"leak_pattern={gate.leak_pattern!r}"))
                self.stdout.write(
                    self.style.ERROR(
                        f"[{index + 1}] 契約違約  {elapsed:.1f}s  "
                        f"leak_pattern={gate.leak_pattern!r}"
                    )
                )
                self.stdout.write(f"      原始輸出前 200 字：{raw[:200]!r}")
                # 違約時實際流程會帶修正提示重試一次；這裡不重試，讓數字直接
                # 反映「一次就過」的比率。仍把 salvage 後的內容接回歷史，讓
                # 後續輪次還是在一個像樣的對話脈絡上跑。
                session.add_agent_message(gate.reply or "（略）")

        return passed, failures


async def _collect(agent, session) -> str:
    """跑實際的串流路徑並收完整段——跟 consumers 的 gate 一模一樣的取用方式。"""
    parts = []
    async for chunk in agent.astream_respond(session):
        parts.append(chunk)
    return "".join(parts)
