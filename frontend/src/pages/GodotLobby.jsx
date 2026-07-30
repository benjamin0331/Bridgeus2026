import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import api from '../api/client';
import './GodotLobby.css';

// Godot web build 是否已放進 public/godot/。抓 .wasm（永遠會匯出、不會被
// SPA fallback 改寫成 index.html），存在才載入 iframe，避免 404 fallback 成
// 「整個 app 塞進 iframe」的重複畫面。
const GODOT_BUILD_PROBE = '/godot/takeAbridge_godot.wasm';

// 把主功能登入的 access token / API base 交給嵌入的 Godot 大廳，並提供拉式
// 發券函式讓 Godot 每次連線前換一次性入場券（不再交付可冒充的 user_id）。
// 同源部署（見 godot-web-deployment-spec.md §0 拓樸表）下可以直接設 iframe
// window，Godot 端 Backend.gd 在需要連線時讀 window.bridgeus_token 並呼叫
// window.bridgeus_request_ticket()（見身份層設計文件 §5.1、§9.1）。
export default function GodotLobby() {
  const iframeRef = useRef(null);
  const navigate = useNavigate();
  const [status, setStatus] = useState('checking'); // checking | ready | missing

  useEffect(() => {
    let cancelled = false;
    fetch(GODOT_BUILD_PROBE, { method: 'HEAD' })
      .then((res) => {
        if (cancelled) return;
        const type = res.headers.get('content-type') || '';
        // SPA fallback 會回 200 + text/html；真正的 wasm 不是 html 才算部署好
        setStatus(res.ok && !type.includes('text/html') ? 'ready' : 'missing');
      })
      .catch(() => { if (!cancelled) setStatus('missing'); });
    return () => { cancelled = true; };
  }, []);

  const handleLoad = () => {
    const el = iframeRef.current;
    if (el) {
      // 印出 Godot 實際可用的畫面範圍，方便對照 Godot 的 viewport 設定。
      const ratio = (el.clientWidth / el.clientHeight).toFixed(3);
      console.log(`[GodotLobby] 前端畫面範圍：${el.clientWidth} x ${el.clientHeight}px（長寬比 ${ratio}）`);
      // Godot 網頁外殼 body 預設黑底、canvas 未必填滿 iframe → 露出黑邊。
      // 同源，直接注入 CSS 強制 canvas 填滿、底色換成 app 米色。
      try {
        const doc = el.contentDocument;
        if (doc && !doc.getElementById('bridgeus-godot-fit')) {
          const style = doc.createElement('style');
          style.id = 'bridgeus-godot-fit';
          style.textContent =
            'html,body{width:100%;height:100%;margin:0;background:#4d4d4d!important;overflow:hidden;}' +
            '#canvas{display:block;width:100%!important;height:100%!important;}';
          doc.head.appendChild(style);
        }
      } catch {
        /* 跨來源時取不到 document，略過（正式同源部署不會發生）*/
      }
    }
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

  // Godot 端配對成功時會 postMessage 通知跳轉（見 game.gd match_found）。
  // 兩個來源檢查都必要：只驗 origin 擋不掉同源的其他 frame，只驗 source
  // 擋不掉惡意站台開的視窗。topic_id 再驗一次型別，路由參數不能信任外部輸入。
  useEffect(() => {
    const handleMessage = (event) => {
      if (event.origin !== window.location.origin) return;
      if (event.source !== iframeRef.current?.contentWindow) return;
      const data = event.data;
      if (data?.type !== 'bridgeus_match') return;
      const topicId = Number(data.topic_id);
      if (!Number.isInteger(topicId) || topicId <= 0) return;
      navigate(`/topic/${topicId}?mode=match&from=godot`);
    };
    window.addEventListener('message', handleMessage);
    return () => window.removeEventListener('message', handleMessage);
  }, [navigate]);

  // 早退出必須排在所有 hook 之後，否則 status 一變成 'missing' 就會少跑上面
  // 那個 useEffect，違反 Rules of Hooks。
  if (status === 'missing') {
    return (
      <div className="godot-lobby godot-lobby--empty">
        <div className="godot-lobby-notice">
          <h2>虛擬大廳尚未部署</h2>
          <p>找不到 Godot 的 web build，請先把 Godot 專案匯出成 HTML5。</p>
        </div>
      </div>
    );
  }

  return (
    <div className="godot-lobby">
      {status === 'checking' ? (
        <div className="godot-lobby-notice">載入虛擬大廳中…</div>
      ) : (
        <iframe
          ref={iframeRef}
          src="/godot/takeAbridge_godot.html"
          onLoad={handleLoad}
          allow="microphone"
          className="godot-lobby-frame"
          title="BridgeUs 虛擬大廳"
        />
      )}
    </div>
  );
}
