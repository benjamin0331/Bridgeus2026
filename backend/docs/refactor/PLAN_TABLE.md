# BridgeUs Backend Refactor — 計畫總表(人類閱讀版)

搭配:`Refactor_Agent執行計畫.md`(給 Agent 的完整版)、`Backend_CodeReview_Refactor準備報告.md`(問題來源)。

## 計畫表

| Part | 階段名稱 | 主要工作 | App | 主要模型 | 備用模型 | 新 Context | 開工前 Review | Review 程度 | 用量 | 預期輸出 | 驗收方式 | 交接對象 | 主要風險 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 基線建立 | 跑測試記基線、建交接目錄 | Codex | GPT-5.4 | Haiku 4.5 | 是 | 不需 | — | 低 | HANDOFF_P0 | 測試指令可重現 | P1 | 測試依賴外部服務跑不全 |
| 1a | 並發修復-設計 | semantic_tree 鎖切分方案 | Claude | **Fable 5** | Opus→GPT-5.6 | 是 | 不需 | — | 高(量小) | 一頁鎖方案 | 方案涵蓋兩個函式+衝突規則 | P1b | Fable 額度不足→降級鏈 |
| 1b | 並發修復-實作 | 鎖外呼叫 LLM+AI session 加鎖 | Codex | GPT-5.5 | Sonnet 5 | 是 | 需 | 快速(只看方案) | 中 | 改版+並發測試 | 交易內無 LLM 呼叫、測試綠 | P2 | 寫回衝突策略體驗影響 |
| 2 | 安全管線統一 | REST/WS 共用 post_match_message | Claude | Sonnet 5 | GPT-5.5 | 是 | 需 | 只看交接文件 | 中 | 新 service+測試 | 兩路徑過濾行為一致 | P3 | REST 無互動通道→降級為擋下 |
| 3 | 快修包 | N+1、serializer、WS 防禦、限流 | Codex | GPT-5.4 | Haiku 4.5 | 是(4 項共用) | 需 | 只看交接文件 | 低 | 4 commits+測試 | assertNumQueries 常數、429 生效 | P4 | serializer 改動影響前端 |
| 4a | 服務抽取-清單 | views helper 分組對照表 | Claude | Opus 4.8 | GPT-5.6 | 是 | 不需 | — | 中(量小) | move map | 函式名皆真實存在(抽查) | P4b | 分組錯誤→搬移時修正 |
| 4b | 服務抽取-搬移 | 照表機械搬移、改 import | Codex | GPT-5.4 | Haiku 4.5 | 是 | 需 | 抽查對照表 | 低~中 | services/+瘦身 views | 全測試綠、無反向 import | P5 | 隱藏循環 import→留原地標記 |
| 5a | 狀態單一化-設計 | session 單一 source of truth 方案 | Claude | **Fable 5** | Opus→GPT-5.6 | 是 | 需(帶 P1 方案) | 完整(僅設計文件) | 高(量小) | 一頁設計文件 | **人工過目放行** | P5b | 與 P1 鎖方案衝突→設計必帶 |
| 5b | 狀態單一化-實作 | 改 service、migration、測試 | Claude | Sonnet 5 | GPT-5.5 | 是 | 需 | 只看設計文件 | 中 | 改版+一致性測試 | 並發不遺失 history(測試證明) | P6 | 舊資料相容→migration 策略寫明 |
| 6 | 清理包 | env 收斂、topic102 外移、死碼、文件 | Codex | GPT-5.4 | Haiku 4.5 | 是 | 需 | 只看交接文件 | 低 | 4 commits | grep 驗證、config 缺欄位會 raise | P7 | fixture 需補必填欄位 |
| 7 | 最終整合 Review | diff review、回歸、結案報告 | Claude | **Fable 5**/Opus | GPT-5.6 | 是(可分段) | 需(讀全部 HANDOFF) | P1/P5 逐行,其餘快掃 | 高 | CLOSEOUT.md | blocker=0、逐項核銷 | 維護者 | diff 過大→分段 review |

**依賴與可並行**:P0 → {P1 ∥ P2} → P3 → P4 → P5 → P6 → P7。
(P3 與 P2 都改 consumers.py/views.py,**不可並行**,P3 排在 P2 後。)
**額度緊時優先序**:P1 > P2 > P3 > P4 > P5 > P6,P7 永遠最後。
**開發位置**:在原 repo 就地 refactor,**不開新專案**。
⚠️ feat/Light 與 dev 已分岔,被 review 的程式碼在 Light 上 → **baseline = feat/Light**(前置先把 dev 併進來)。
**流程**:每 Part 開子分支 `refactor/m{模組}-p{n}-{slug}` → 你 review 子分支 diff → 併回 feat/Light → 全部完成後 PR `feat/Light → dev`。
**前置(人工)**:先 `git merge origin/dev` 進 feat/Light、解決 reasoning-mode 重疊、測試綠,再開 P0。

## 簡化流程圖

```
P0 基線 (GPT-5.4)
  │
  ├──────────────┐
  ▼              ▼
P1 並發修復    P2 安全管線          ← 兩者檔案無交集,可並行
(Fable設計→    (Sonnet 5)
 GPT-5.5實作)     │
  │              ▼
  │           P3 快修包 (GPT-5.4)   ← 與 P2 同檔案,必須在 P2 之後
  │              │
  └──────┬───────┘
         ▼  各自產 HANDOFF
P4 服務抽取 (Opus 清單 → GPT-5.4 搬移)
         │  HANDOFF_P4 + move map
         ▼
P5 狀態單一化 (Fable 設計 → 人工放行 → Sonnet 實作)
         │  HANDOFF_P5 + 設計文件
         ▼
P6 清理包 (GPT-5.4)
         │  HANDOFF_P6
         ▼
P7 最終整合 Review (Fable/Opus, 分段 diff)
         │
         ▼
CLOSEOUT.md + 對照 code review 報告逐項核銷
```

## 高階模型(Fable/Opus/GPT-5.6)只出現三次

1. **P1a** 鎖切分設計 — 錯了最難察覺
2. **P5a** 資料一致性設計 — 全案唯一人工放行點
3. **P7** 最終 diff review — 高階模型核心價值場景

其餘全部走中低階(Sonnet/GPT-5.5 或 Haiku/GPT-5.4),交接靠 repo 內 `backend/docs/refactor/handoffs/HANDOFF_P{n}.md`,任何 Agent 不需讀歷史對話。
