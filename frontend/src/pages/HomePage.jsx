import IssueCard from '../components/IssueCard';
import ActionCard from '../components/ActionCard';
import './HomePage.css';

function HomePage({ navigate, userName, issues, issuesLoaded, entryMode }) {
    return (
        <div className="content-area">
            {/* 使用者歡迎詞區域 */}
            <h1 className="greeting-text">早安！{userName}</h1>
            <h2 className="sub-greeting-text">今天想討論點什麼？</h2>
            
            {/* 主要儀表板佈局網格 */}
            <div className="dashboard-grid">
                {/* 議題列表元件：傳遞導航方法與議題資料 */}
                <IssueCard navigate={navigate} issues={issues} issuesLoaded={issuesLoaded} entryMode={entryMode} />
                
                {/* 右側快捷操作按鈕堆疊區域 */}
                <aside className="action-button-stack">
                    {/* 進入觀點知識庫 */}
                    <ActionCard 
                        text="觀點知識庫" 
                        className="knowledge-base-entry" 
                        onClick={() => navigate('/kb')} 
                    />
                    
                    {/* 進入虛擬大廳：使用 SPA 路由跳轉以維持應用程式狀態 (避免開啟新分頁) */}
                    <ActionCard
                        text="虛擬大廳"
                        className="virtual-chat-entry"
                        onClick={() => navigate('/chat')} 
                    />
                </aside>
            </div>
        </div>
    );
}

export default HomePage;
