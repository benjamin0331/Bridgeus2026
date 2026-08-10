import React from 'react';
import './FavoriteStarButton.css';

// 收藏星星：實心＝已收藏，空心＝未收藏。點擊只切換收藏狀態，
// 不應該觸發外層卡片/連結的導頁，所以一律 stopPropagation + preventDefault。
const FavoriteStarButton = ({ active, onToggle, className = '' }) => {
  const handleClick = (event) => {
    event.stopPropagation();
    event.preventDefault();
    onToggle();
  };

  return (
    <button
      type="button"
      className={`favorite-star-btn${active ? ' favorite-star-btn--active' : ''}${className ? ` ${className}` : ''}`}
      onClick={handleClick}
      aria-pressed={active}
      aria-label={active ? '取消收藏' : '加入收藏'}
      title={active ? '取消收藏' : '加入收藏'}
    >
      <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
        <path
          d="M12 2.5l2.9 6.02 6.6.79-4.86 4.6 1.28 6.55L12 17.9l-5.92 2.56 1.28-6.55-4.86-4.6 6.6-.79z"
          fill={active ? 'currentColor' : 'none'}
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinejoin="round"
        />
      </svg>
    </button>
  );
};

export default FavoriteStarButton;
