// children 是給裝飾層用的（見 ScatterDecor）。卡片的文字仍走 text prop，
// 所以既有呼叫端一行都不用改。
//
// 文字 span 有自己的 class：不能讓 CSS 用「.card > span」去指它，那樣會連裝飾層
// 一起命中（裝飾層也是直接的 span 子元素），把它的 position: absolute 覆寫掉。
function ActionCard({ text, onClick, className, children }) {
  return (
    <div className={`action-card ${className}`} onClick={onClick}>
      <span className="action-card-text">{text}</span>
      {children}
    </div>
  );
}

export default ActionCard;
