import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import api from '../api/client';
import './ConsentPage.css';

// 全文與版本號都由後端 GET /api/consent/ 提供（見 backend/accounts/consent.py
// 的 CONSENT_DOCUMENT / CONSENT_VERSION），這個頁面只負責把回傳的結構渲染
// 出來。改版時只要改後端那個檔案、順手 bump CONSENT_VERSION，前端不用動。
function ConsentPage() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .get('/api/consent/')
      .then((response) => {
        if (!cancelled) setData(response.data);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="consent-page">
      <div className="consent-card">
        {error ? (
          <p className="consent-loading">目前無法載入研究參與說明，請稍後再試。</p>
        ) : !data ? (
          <p className="consent-loading">載入中…</p>
        ) : (
          <>
            <div className="consent-header">
              <span className="consent-eyebrow">{data.document.eyebrow}</span>
              <h1>{data.document.title}</h1>
              <span className="consent-version">
                版本 {data.version} ・ {data.document.meta.published_date} 發布
              </span>
            </div>

            {data.document.sections.map((section) => (
              <div className="consent-section" key={section.heading}>
                <h2>{section.heading}</h2>
                {section.paragraphs.map((paragraph, index) => (
                  <p key={index}>{paragraph}</p>
                ))}
                {section.list_items.length > 0 && (
                  <ol>
                    {section.list_items.map((item, index) => (
                      <li key={index}>{item}</li>
                    ))}
                  </ol>
                )}
              </div>
            ))}

            <div className="consent-meta">
              <div className="consent-meta-row">
                <span>發布單位</span>
                <span>{data.document.meta.issuer}</span>
              </div>
              <div className="consent-meta-row">
                <span>發布日期</span>
                <span>{data.document.meta.published_date}</span>
              </div>
              <div className="consent-meta-row">
                <span>最近更新日期</span>
                <span>{data.document.meta.updated_date}</span>
              </div>
            </div>
          </>
        )}

        <div className="consent-footer">
          <Link to="/register" className="consent-back-link">
            返回註冊
          </Link>
        </div>
      </div>
    </div>
  );
}

export default ConsentPage;
