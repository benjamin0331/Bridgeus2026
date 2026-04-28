function ActionCard({ text, onClick, className }) {
  return (
    <div className={`action-card ${className}`} onClick={onClick}>
      <span>{text}</span>
    </div>
  );
}

export default ActionCard;