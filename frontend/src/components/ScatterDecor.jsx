import './ScatterDecor.css';

// 虛擬大廳卡片上的散落裝飾：各等級青蛙（含稀有款炫彩）＋ 遊戲內表情，
// 大小、角度、朝向都不規則，全部是不會動的 PNG。
//
// 座標是「隨機擲一次」的結果，寫死在這裡而不是每次 render 用 Math.random()：
// 亂數會讓每次 re-render 都跳位、熱重載時圖案一直變，看起來像 bug。
// 要重新洗牌就跑 scratch 的 gen_scatter.py（固定種子 + 不重疊 + 避開左上標題區 +
// 每個位置取離已放置元素最遠的候選，避免全擠在右半邊），把輸出貼回來。
//
// 每一項：src 圖檔、top/left 百分比、rot 角度、w 寬度(px)、o 不透明度、flip 水平翻轉。
// 沒有白色的 lv0：它在草地背景上幾乎看不見。
const ITEMS = [
  { src: '/frogs/lv777.png', top: 50.3, left: 17.1, rot: -8, w: 36, o: 0.88 },
  { src: '/frogs/lv1.png', top: 8.1, left: 91.3, rot: -8, w: 30, o: 0.82 },
  { src: '/frogs/lv2.png', top: 85.5, left: 65.5, rot: 22, w: 32, o: 0.88 },
  { src: '/frogs/lv3.png', top: 2.9, left: 50.1, rot: 6, w: 26, o: 0.8 },
  { src: '/frogs/lv4.png', top: 60.5, left: 90.9, rot: 21, w: 30, o: 0.94, flip: true },
  { src: '/frogs/lv5.png', top: 51.4, left: 45.3, rot: 24, w: 28, o: 0.93 },
  { src: '/frogs/lv6.png', top: 88.8, left: 2.1, rot: 0, w: 24, o: 0.83, flip: true },
  { src: '/frogs/emoji0.png', top: 33.7, left: 70.2, rot: -6, w: 22, o: 0.83, flip: true },
  { src: '/frogs/emoji2.png', top: 89.1, left: 33.1, rot: 17, w: 19, o: 0.91 },
  { src: '/frogs/emoji3.png', top: 46.4, left: 1.7, rot: -11, w: 17, o: 0.83 },
  { src: '/frogs/emoji4.png', top: 2.8, left: 68.1, rot: -4, w: 20, o: 0.83, flip: true },
];

function ScatterDecor() {
  return (
    <span className="scatter-decor" aria-hidden="true">
      {ITEMS.map((it) => (
        <img
          key={it.src}
          src={it.src}
          alt=""
          style={{
            top: `${it.top}%`,
            left: `${it.left}%`,
            width: `${it.w}px`,
            opacity: it.o,
            // 翻轉要寫在 rotate 前面：先鏡射再轉，角度才是視覺上看到的方向。
            transform: `${it.flip ? 'scaleX(-1) ' : ''}rotate(${it.rot}deg)`,
          }}
        />
      ))}
    </span>
  );
}

export default ScatterDecor;
