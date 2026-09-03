import './OnboardingCard.css';

// 設定頁的「新手導覽」卡片：重看一次首次登入時跳出來的那份導覽。
//
// 這裡只負責按鈕，導覽本身掛在 App.jsx 最外層（它要蓋住導覽列與側邊功能欄，
// 掛在頁面裡蓋不住），所以開關是從上面傳下來的 onReplayTour。
//
// 重看不會把後端的「已看過」旗標清掉：手動重看跟「還沒看過」是兩件事，清掉
// 會讓下次登入又自動跳一次。導覽關閉時 App 仍會送一次標記完成的 PATCH，
// 後端只在第一次蓋時間戳，所以重送是安全的。
function OnboardingCard({ onReplayTour }) {
  return (
    <section className="settings-create-form onboarding-card">
      <h2>新手導覽</h2>
      <p className="settings-hint onboarding-card-hint">
        第一次登入時出現的功能介紹，隨時可以再看一次。
      </p>
      <div className="onboarding-card-actions">
        <button type="button" onClick={onReplayTour}>
          重看新手導覽
        </button>
      </div>
    </section>
  );
}

export default OnboardingCard;
