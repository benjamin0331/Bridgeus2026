// 網頁跳轉
import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom' // 引入路由器
import App from './App.jsx'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter> {/* 讓整個專案支援網址跳轉 */}
      <App />
    </BrowserRouter>
  </React.StrictMode>,
)
