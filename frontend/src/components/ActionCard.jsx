function ActionCard({ text, onClick, className }) {
  return (
    <div className={`action-card ${className}`} onClick={onClick}>
      <span className="action-card-text">{text}</span>
    </div>
  );
}

export default ActionCard;
