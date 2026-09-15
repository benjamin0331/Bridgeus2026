import './AccountSwitchedOverlay.css';

/**
 * 這個瀏覽器已經切換到另一個帳號時蓋住整個畫面。
 *
 * 為什麼要「蓋住」而不只是提示：同一個瀏覽器的所有無痕視窗共用 localStorage，
 * 實驗現場又常常是共用機器，畫面上留著的是**上一位受試者**的對話內容。只掛
 * 一條警告條的話，內容仍然看得見，匿名性就破了。
 *
 * 為什麼不直接強制登出：後測問卷與進行中的對話都可能填到一半。問卷有
 * localStorage 草稿保護，對話沒有——直接踢掉就是丟掉一個樣本。這裡讓使用者
 * 自己選，兩條路都不會默默吃掉狀態。
 */
export default function AccountSwitchedOverlay({ onReload, onLogout }) {
  return (
    <div className="account-switched-overlay" role="alertdialog" aria-modal="true">
      <div className="account-switched-card">
        <h2>這個瀏覽器已切換到另一個帳號</h2>
        <p>
          在同一個瀏覽器的其他視窗登入了別的帳號。瀏覽器的登入資料是共用的，
          所以這個分頁現在的身分已經不是原本那一位了——為了避免把操作記到錯的
          帳號上，已經先停止送出任何請求。
        </p>
        <p className="account-switched-hint">
          要同時測試多個帳號，請改用不同的<strong>瀏覽器設定檔</strong>或不同的瀏覽器。
          無痕視窗彼此並不隔離，開再多個都是同一份登入資料。
        </p>
        <div className="account-switched-actions">
          <button type="button" className="account-switched-primary" onClick={onReload}>
            以目前帳號重新載入
          </button>
          <button type="button" className="account-switched-secondary" onClick={onLogout}>
            登出並回到登入頁
          </button>
        </div>
        <p className="account-switched-hint">
          登出會一併登出這個瀏覽器的其他視窗（登入資料是共用的）。
        </p>
      </div>
    </div>
  );
}
