import { Link } from 'react-router-dom';

// 全文內容由研究團隊提供，目前是佔位文字。替換內容不需要改任何其他檔案；
// 若同意書有實質變動，記得同步更新後端 accounts/consent.py 的 CONSENT_VERSION，
// 否則新舊兩版的簽署紀錄會混在同一個版本字串底下。
function ConsentPage() {
  return (
    <div style={{ maxWidth: 720, margin: '0 auto', padding: 24 }}>
      <h1>研究參與說明</h1>
      <p>（此處為同意書全文，內容待研究團隊提供。）</p>
      <p>
        <Link to="/register">返回註冊</Link>
      </p>
    </div>
  );
}

export default ConsentPage;
