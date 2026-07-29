import { useEffect, useRef, useState } from 'react';
import api, { getAccessTokenPayload } from '../api/client';
import './GodotLobby.css';

// Godot web build 是否已放進 public/godot/。抓 .wasm（永遠會匯出、不會被
// SPA fallback 改寫成 index.html），存在才載入 iframe，避免 404 fallback 成
// 「整個 app 塞進 iframe」的重複畫面。
const GODOT_BUILD_PROBE = '/godot/takeAbridge_godot.wasm';

// 把主功能登入的 access token / user_id / API base 交給嵌入的 Godot 大廳。
// 同源部署（見 godot-web-deployment-spec.md §0 拓樸表）下可以直接設 iframe
// window，Godot 端 Backend.gd::acquire_token_from_host() 在 _ready() 就會讀
// window.bridgeus_token / window.bridgeus_user_id（見同檔 §2）。
export default function GodotLobby() {
  const iframeRef = useRef(null);
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

    const token = localStorage.getItem('access') || '';
    const userId = getAccessTokenPayload()?.user_id ?? 0;
    const apiBase = `${api.defaults.baseURL || ''}/api`;

    frameWindow.bridgeus_token = token;
    frameWindow.bridgeus_user_id = userId;
    frameWindow.bridgeus_api_base = apiBase;
    // 多人連線位址：同源拓樸下 /godot-ws 由 Cloudflare Tunnel 轉到 headless
    // Godot server（見部署 runbook）。用當前 origin 自動組，不寫死網域；
    // https 頁面自動用 wss。本機直接開 build（不經此頁）時不會被設，Godot 端
    // _resolve_connection_settings() 會退回 ws://127.0.0.1:8085。
    const wsProto = location.protocol === 'https:' ? 'wss' : 'ws';
    frameWindow.bridgeus_ws_url = `${wsProto}://${location.host}/godot-ws`;
  };

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
