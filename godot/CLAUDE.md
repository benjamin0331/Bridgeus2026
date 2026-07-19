# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 語言

一律以**繁體中文**回答使用者。

## What this is

BridgeUs — a 2D social-RPG prototype in **Godot 4.7** (GL Compatibility renderer, d3d12 on Windows). Two players meet in a shared world, post a discussion 議題 (issue) above their head, walk up to each other, read each other's issue, and start a 1-on-1 chat. The whole flow runs on **Godot's own multiplayer** (`WebSocketMultiplayerPeer`, port 8080); a Django backend is reserved for later behind a stub (see below).

## Running

No build/lint/test CLI — this is an editor-driven Godot project. Open it in the Godot 4.7 editor and press Play, or `godot --path .`. Main scene is `World/Game.tscn`.

To exercise multiplayer you need **two running instances**: one clicks **Host** (`create_server`), the other clicks **Join** (`create_client` to `127.0.0.1:8080`). In the editor: Debug → Run Multiple Instances → 2. There is no automated test harness despite the "multiplayer synchronization test" commit.

## Architecture — the load-bearing decisions

**All cross-peer RPCs live on the player node, never on the UI.** A player is named `str(peer_id)` and lives at `/root/Game/<peer_id>` — an *identical path on every peer*, which is what makes `rpc_id` targeting work. The UI (`UI/issue_ui.gd`) is a screen-space `CanvasLayer` that is **local-only and contains zero RPCs**; it calls into the local player for anything networked, and the player calls back into the UI (found via the `issue_ui` group) to display incoming events. Keep this split — moving an RPC onto the UI breaks path resolution across peers.

**Spawn flow:** `World/game.gd` Host/Join buttons set up the peer. A joining client waits ~0.2s for the handshake, then `request_spawn.rpc_id(1, my_id)`; only the server runs `_spawn_player`, which `add_child`s `player_00.tscn` named `str(id)` under `Game`. The `MultiplayerSpawner` (`spawn_path=".."` → `Game`) replicates it to everyone. Each player sets its own multiplayer authority from its name in `_enter_tree`; `is_multiplayer_authority()` gates input, camera, and proximity detection so only the local player is driven.

**Random appearance & animation:** all character looks live as animation pairs (`char_N_idle` / `char_N_run`) in the single player scene's one `AnimatedSprite2D` SpriteFrames — no per-character scene. At spawn the authority rolls `appearance = randi() % <count>` (count derived from the `char_N_idle` animations, so adding a character = add its two animations, no code change). `appearance` + a `moving` bool are synced via the MultiplayerSynchronizer (`spawn=true`, so late joiners get the right look); every peer's `_process` plays `char_{appearance}_{idle|run}` and `flip_h` (synced) handles left-facing. Sizing: `_update_anim` normalizes every character to `SPRITE_PX` tall via `scale = SPRITE_PX / frame_height`, applied **once per appearance** (tracked by `_scaled_for`, decoupled from idle/run switching — else the scale only landed on the first animation change and the sprite visibly resized when it started moving). With uniform-spec art (all frames the same px) this yields one scale for everyone.

**Issue broadcast & live refresh:** `submit_issue` → `apply_issue.rpc(...)` (`@rpc("authority","call_local","reliable")`) runs on every peer, sets `issue_title/_body`, updates the head bubble, and calls `ui.refresh_menu()` so a bystander already standing in proximity sees「（對方尚未提交議題）」flip to the real title without re-entering range. **Late joiners get issues too**: on connect, `game.gd` fires `request_issue_sync.rpc()`, and every authority re-sends its issue (and banner/reactions) to the requester via `apply_*.rpc_id`. This is the one late-join catch-up path — RPCs alone don't reach peers who join later.

**Issue reactions (表情回復):** while reading someone's issue, you pick **one** of 5 emoji (Read panel bottom-row buttons) — one reaction per reader per issue. The emojis live in `Globals/Emoji.gd` — a `class_name Emoji` static atlas table (16×16 grid) shared by buttons, head display, and the count column; `Emoji.tex(i)` / `Emoji.REGIONS`. Not an autoload (pure data). Flow mirrors issues: reader → `react_to_issue` → `add_reaction.rpc_id(author)` → **author holds one-per-reader, changeable** via `_reactor_ids` (a `{sender_id: idx}` dict keyed on `get_remote_sender_id()`, authority-only, not synced — reacting overwrites that sender's value; `reactions = _reactor_ids.values()` is derived from it, so a re-pick swaps the emoji in place without reordering the head row) → `apply_reactions.rpc(...)` broadcasts the whole idx list → every peer rebuilds the world-space head row (overlapping ~half via HBox `separation=-8`, no background, capped at `HEAD_MAX`=10 then「…」). Because the head is ambiguous at a glance, the Read panel's **right column** lists all 5 emoji with per-idx counts (`emoji ×N`), tallied from `_target.reactions` and refreshed live via `on_reactions_changed`. Reader-side, `_reacted_targets` tracks the reader's current pick per target; the chosen one shows a gray bottom bar and pressing another emoji re-picks (moves the bar, overwrites author-side). `apply_issue` clears both `reactions` and `_reactor_ids` (re-submit/delete wipes reactions) and calls `on_issue_reset` so readers' local locks clear too. Late joiners get reactions via `request_issue_sync`, sent **after** the issue (apply_issue clears, so order matters).

**Invite/chat handshake** is a chain of `@rpc("any_peer","reliable")` calls between the two players' nodes (`receive_invite` → `invite_result` → `send_chat`/`receive_chat` → `peer_left_chat`). Godot 4 `server_relay` (on by default) relays client↔client RPCs through the host, so host and client are symmetric.

## Conventions that bit us (don't relitigate)

- **Collision layers:** players are on **layer 2, mask 1** so they pass *through* each other but still collide with the environment (`Boundry`/walls must stay on layer 1, "Environment"). Without this, `move_and_slide` overlap-recovery shoves the other player. The proximity `Area2D` uses `collision_mask = 2` and group-filters to `players` in its callback.
- **Head bubble** lives in *world space* under the player, so the 2× camera zoom doubles it: font is rasterized at 18px and the box scaled `BUBBLE_SCALE = 0.5` to land crisp 1:1 on screen. `BUBBLE_Y` is the vertical offset above the player origin — a hand-tuned constant (the sprite *frame* is 144px tall but the visible creature's head sits near y≈−10), adjust it in the editor by eye.

## Backend seam

`Globals/Backend.gd` is the single seam to the Django backend — every network-facing call routes through it via native `HTTPRequest` (`BASE_URL = http://localhost:8005/api`). `guest_login` and `submit_issue` are **live**; `request_topic_match` is still a stub (prints, returns code 0); `analyze_stance/_emotion` return null (intentionally unimplemented, nullable placeholders reserved). The backend is optional — no server = local-only play (issues still broadcast, just not persisted). The real Django backend lives in a separate repo; the full integration plan + endpoint specs are in `docs/README.md` §四.

## Style

GDScript with leading-underscore for private members/helpers. Code comments here carry real rationale — read them before changing collision masks, RPC modes, or the bubble scaling. Comments tagged `ponytail:` mark deliberate prototype shortcuts.

Note: the live NPC dialogue is `UI/dialogue_manager.tscn` (a `CanvasLayer` scripted by `UI/dialogue_box.gd`), instanced in `World/Game.tscn`. The old experimental dialogue/player variants have been removed.
