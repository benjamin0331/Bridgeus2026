# 貢獻指南 — Take A Bridge (橋得攏)

所有組員在開始開發前請完整閱讀本文件。

---

## 分支策略

```
main              ← 穩定版本，永遠可部署，僅透過 PR 合併
└── dev           ← 整合分支，功能完成後先合併到這裡
    ├── feat/benjamin
    ├── feat/polarbear
    ├── feat/hsiao
    ├── feat/Light
    └── feat/Ceeeuu
```

**規則：**
- 所有人在自己的 `feat/<name>` 分支開發
- 完成後發 PR → `dev`，需至少 **1 人 review** 才能合併
- `dev` → `main` 的 PR 需至少 **2 人 review**（含 Benjamin）
- **禁止直接 push 到 `main` 或 `dev`**

---

## 開始開發（第一次）

```bash
git clone https://github.com/bridgeus2026/Bridgeus2026.git
cd Bridgeus2026

# 切換到自己的分支（以 polarbear 為例）
git checkout feat/polarbear
```

---

## 日常開發流程

```bash
# 1. 開發前先同步 dev 的最新進度
git checkout dev
git pull origin dev

# 2. 切回自己的分支，並 rebase 同步
git checkout feat/<your-name>
git rebase dev

# 3. 寫程式...

# 4. Commit（格式見下方）
git add <files>
git commit -m "feat(m1): implement JWT login endpoint"

# 5. 推送
git push origin feat/<your-name>

# 6. 在 GitHub 上發 Pull Request → dev
```

---

## Commit 格式（Conventional Commits）

```
<type>(<scope>): <subject>
```

### Type

| Type | 用途 | 範例 |
|------|------|------|
| `feat` | 新功能 | `feat(m3): implement cosine similarity matching` |
| `fix` | Bug 修復 | `fix(m4): resolve WebSocket reconnection loop` |
| `docs` | 文件 | `docs(api): update M2 stance endpoint schema` |
| `refactor` | 重構（不改功能） | `refactor(m1): extract JWT logic to middleware` |
| `test` | 測試 | `test(m5): add unit tests for embedding distance` |
| `chore` | 雜務（CI、依賴等） | `chore: update requirements.txt` |
| `style` | 格式（不影響邏輯） | `style(m2): fix PEP8 indentation` |

### Scope（模組代號）

| Scope | 對應模組 |
|-------|---------|
| `m1` | User Auth |
| `m2` | Topic Selection & Stance Measurement |
| `m3` | Heterogeneous Matching & AI Agent |
| `m4` | Real-time Dialogue Room |
| `m5` | NLP Analysis & CCND |
| `m6` | Post-Dialogue Summary & KB |
| `core` | 跨模組共用 |

### 規則
- Subject 用**英文**、小寫開頭、不加句號、不超過 72 字元
- Body 可中英混合，解釋 **why**，不是 what
- Breaking change 加 `BREAKING CHANGE:` footer

---

## Pull Request 規範

**PR Title**：同 commit 格式，如 `feat(m3): implement stance vector matching`

**PR 內容（請填寫以下欄位）：**

```markdown
## What
簡述這個 PR 做了什麼

## Why
為什麼需要這個改動

## Module
- [ ] M1 Auth
- [ ] M2 Topic & Stance
- [ ] M3 Matching & AI Agent
- [ ] M4 Dialogue Room
- [ ] M5 NLP & CCND
- [ ] M6 Summary & KB

## Checklist
- [ ] 本地測試通過
- [ ] 無 hardcoded secrets（API key、密碼等）
- [ ] API 變更已更新 docs/TakeABridge_API_Spec.md
- [ ] 有需要的話已加註解
```

---

## 注意事項

- **絕對不要** commit `.env` 檔案（內含 API keys）
- 複製 `.env.example` → `.env`，填入自己的 key，只留在本地
- 有衝突先解決再發 PR，不要強制 push

---

## 相關文件

| 文件 | 說明 |
|------|------|
| [README.md](./README.md) | 專案簡介與環境設定 |
| [docs/TakeABridge_API_Spec.md](./docs/TakeABridge_API_Spec.md) | 模組間 API 合約 |
| [CLAUDE.md](./CLAUDE.md) | 系統架構完整說明（AI assistant 用） |
