import { useRef } from 'react';
import api from '../api/client';
import './GodotLobby.css';

// 把主功能登入的 access token / API base 交給嵌入的 Godot 大廳，並提供拉式
// 發券函式讓 Godot 每次連線前換一次性入場券（不再交付可冒充的 user_id）。
// 同源部署（見 godot-web-deployment-spec.md §0 拓樸表）下可以直接設 iframe
// window，Godot 端 Backend.gd 在需要連線時讀 window.bridgeus_token 並呼叫
// window.bridgeus_request_ticket()（見身份層設計文件 §5.1、§9.1）。
export default function GodotLobby() {
  const iframeRef = useRef(null);

  const handleLoad = () => {
    const frameWindow = iframeRef.current?.contentWindow;
    if (!frameWindow) return;

    // JWT 保留給 Godot client 自己打議題/頭銜 API 用——iframe 與主功能同源、
    // 同一個瀏覽器信任域，跟 React 把 token 放 localStorage 是同一件事。
    // 不再交 user_id：對遊戲 server 的身份識別改走一次性入場券（見下），
    // client 不需要、也不該知道要自報什麼 id（自報的 id 就是可冒充的 id）。
    frameWindow.bridgeus_token = localStorage.getItem('access') || '';
    frameWindow.bridgeus_api_base = `${api.defaults.baseURL || ''}/api`;

    // 拉式發券：Godot 每次要連線前呼叫這個函式，完成後把券寫進 bridgeus_ticket
    // （Godot 端輪詢）。不能在 iframe 載入時就發——券是一次性、60 秒到期，
    // 撐不過 WASM 冷啟動，重連時更會拿著已兌換的券被踢（見 spec §5.1）。
    //
    // 三種狀態靠 bridgeus_ticket 的值區分：undefined=還沒要過、''=請求中、
    // 'ERROR'=要不到、其他=券本身。後端的券是 secrets.token_urlsafe(32)，
    // 永遠不可能等於 'ERROR'，所以這個哨兵值不會跟真券撞號。
    //
    // seq 是為了防競態：連續呼叫兩次時，先發的請求可能後回，沒有這個守衛就會
    // 用舊結果蓋掉新結果。只有最後一次呼叫的回應能寫入。
    let ticketSeq = 0;
    frameWindow.bridgeus_request_ticket = () => {
      const mySeq = ++ticketSeq;
      frameWindow.bridgeus_ticket = '';
      api.post('/api/godot/tickets/')
        .then((res) => {
          if (mySeq !== ticketSeq) return;
          frameWindow.bridgeus_ticket = res.data.ticket;
        })
        .catch((err) => {
          if (mySeq !== ticketSeq) return;
          console.error('[GodotLobby] 入場券取得失敗', err);
          frameWindow.bridgeus_ticket = 'ERROR';
        });
    };

    // 多人連線位址：同源拓樸下 /godot-ws 由 Cloudflare Tunnel 轉到 headless
    // Godot server（見部署 runbook）。用當前 origin 自動組，不寫死網域；
    // https 頁面自動用 wss。本機直接開 build（不經此頁）時不會被設，Godot 端
    // _resolve_connection_settings() 會退回 ws://127.0.0.1:8085。
    const wsProto = location.protocol === 'https:' ? 'wss' : 'ws';
    frameWindow.bridgeus_ws_url = `${wsProto}://${location.host}/godot-ws`;
  };

  return (
    <div className="godot-lobby">
      <iframe
        ref={iframeRef}
        src="/godot/takeAbridge_godot.html"
        onLoad={handleLoad}
        allow="microphone"
        className="godot-lobby-frame"
        title="BridgeUs 虛擬大廳"
      />
    </div>
  );
}
