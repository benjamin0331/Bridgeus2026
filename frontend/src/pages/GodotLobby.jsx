import { useRef } from 'react';
import api, { getAccessTokenPayload } from '../api/client';
import './GodotLobby.css';

// 把主功能登入的 access token / user_id / API base 交給嵌入的 Godot 大廳。
// 同源部署（見 godot-web-deployment-spec.md §0 拓樸表）下可以直接設 iframe
// window，Godot 端 Backend.gd::acquire_token_from_host() 在 _ready() 就會讀
// window.bridgeus_token / window.bridgeus_user_id（見同檔 §2）。
export default function GodotLobby() {
  const iframeRef = useRef(null);

  const handleLoad = () => {
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
