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
    // window.bridgeus_ws_url 故意不設：常駐 Godot server 的正式網域還沒決定
    // （infra 這輪明確不做，見部署規格 §0/§4），Godot 端 _resolve_connection_settings()
    // 讀不到就會退回本機位址——之後 infra 定案再回來補這個值即可。
  };

  return (
    <div className="godot-lobby">
      <iframe
        ref={iframeRef}
        src="/godot/index.html"
        onLoad={handleLoad}
        allow="microphone"
        className="godot-lobby-frame"
        title="BridgeUs 虛擬大廳"
      />
    </div>
  );
}
