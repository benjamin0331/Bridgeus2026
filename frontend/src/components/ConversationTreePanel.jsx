import { useEffect, useMemo, useRef, useState } from 'react';
import {
  easeCubicOut,
  hierarchy,
  linkRadial,
  max,
  min,
  pointRadial,
  select,
  tree,
  zoom,
} from 'd3';
import './ConversationTreePanel.css';

const EMPTY_DETAIL_TEXT = '點選語意節點後，這裡會顯示完整原始訊息、脈絡路徑與整理理由。';

function cleanText(value) {
  return String(value || '').replace(/\s+/g, ' ').trim();
}

function shortenLabel(text, maxLength) {
  const cleanedText = cleanText(text);
  if (cleanedText.length <= maxLength) {
    return cleanedText || '空白';
  }

  return `${cleanedText.slice(0, maxLength - 1)}…`;
}

function formatSemanticTime(value) {
  if (!value) {
    return '';
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return cleanText(value);
  }

  return new Intl.DateTimeFormat('zh-TW', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date);
}

function nodeShape(type) {
  return type === 'anchor' ? 'rect' : 'circle';
}

function nodeSize(type) {
  if (type === 'root') {
    return { radius: 46, width: 92, height: 92 };
  }

  if (type === 'anchor') {
    return { radius: 0, width: 76, height: 42 };
  }

  return { radius: 30, width: 60, height: 60 };
}

function labelWidth(type) {
  if (type === 'root') {
    return 72;
  }

  if (type === 'anchor') {
    return 62;
  }

  return 48;
}

function labelLineLimit(type) {
  if (type === 'root') {
    return 3;
  }

  if (type === 'anchor') {
    return 2;
  }

  return 3;
}

function labelMaxLength(type) {
  if (type === 'root') {
    return 18;
  }

  if (type === 'anchor') {
    return 10;
  }

  return 16;
}

function normalizeTreeNode(node, depth = 0) {
  if (!node || typeof node !== 'object') {
    return null;
  }

  const id = cleanText(node.id) || `${depth}-${cleanText(node.name) || 'node'}`;
  const name = cleanText(node.name) || '未命名';
  const type = depth === 0 ? 'root' : depth === 1 ? 'anchor' : 'point';
  return {
    ...node,
    id,
    name,
    type,
    children: (Array.isArray(node.children) ? node.children : [])
      .map((child) => normalizeTreeNode(child, depth + 1))
      .filter(Boolean),
  };
}

function normalizeTreeData(treeData, topicTitle) {
  const normalized = normalizeTreeNode(treeData);
  if (normalized) {
    return normalized;
  }

  return {
    id: 'root',
    name: topicTitle || '核電語意樹',
    type: 'root',
    children: [],
  };
}

function normalizeTreeEntries(trees, treeData, topicTitle) {
  const sourceTrees = Array.isArray(trees) && trees.length
    ? trees
    : [
      {
        ownerKey: 'default',
        label: '我的脈絡',
        isCurrentUser: true,
        treeData,
      },
    ];

  return sourceTrees.map((entry, index) => {
    const ownerKey = cleanText(entry.ownerKey) || `tree-${index}`;
    return {
      ownerKey,
      label: cleanText(entry.label) || (entry.isCurrentUser ? '我的脈絡' : '對方脈絡'),
      isCurrentUser: Boolean(entry.isCurrentUser),
      treeData: normalizeTreeData(entry.treeData, topicTitle),
      analyzedSourceIds: Array.isArray(entry.analyzedSourceIds) ? entry.analyzedSourceIds : [],
    };
  });
}

function shouldRenderNode(node) {
  if (node.type !== 'anchor') {
    return true;
  }

  if (!node.hiddenUntilUsed) {
    return true;
  }

  return (node.children || []).length > 0;
}

function deriveVisibleTreeData(node) {
  return {
    ...node,
    children: (node.children || [])
      .filter(shouldRenderNode)
      .map(deriveVisibleTreeData),
  };
}

function findNodeById(node, targetId) {
  if (!node) {
    return null;
  }

  if (node.id === targetId) {
    return node;
  }

  for (const child of node.children || []) {
    const result = findNodeById(child, targetId);
    if (result) {
      return result;
    }
  }

  return null;
}

function findNodePath(node, targetId, path = []) {
  if (!node) {
    return null;
  }

  const nextPath = [...path, node];
  if (node.id === targetId) {
    return nextPath;
  }

  for (const child of node.children || []) {
    const result = findNodePath(child, targetId, nextPath);
    if (result) {
      return result;
    }
  }

  return null;
}

function nodeMessages(node) {
  const messages = Array.isArray(node?.messages) ? [...node.messages] : [];

  if (node?.claimText && !messages.some((message) => message.text === node.claimText)) {
    messages.unshift({
      text: node.claimText,
      stance: node.stance || '中立',
      confidence: node.confidence,
      rationale: node.rationale || '',
      mode: 'legacy',
    });
  }

  if (node?.sourceClaim && !messages.some((message) => message.text === node.sourceClaim)) {
    messages.unshift({
      text: node.sourceClaim,
      stance: '中立',
      mode: 'source',
    });
  }

  return messages;
}

function nodeCollisionRadius(type) {
  const size = nodeSize(type);
  if (type === 'anchor') {
    return Math.hypot(size.width / 2, size.height / 2) + 12;
  }

  return size.radius + 10;
}

function clamp(value, floor, ceiling) {
  return Math.max(floor, Math.min(ceiling, value));
}

function applyPolarConstraints(node) {
  if (!node.isCollisionMovable) {
    return;
  }

  let angle = Math.atan2(node.px, -node.py);
  while (angle - node.sectorCenterAngle > Math.PI) angle -= Math.PI * 2;
  while (angle - node.sectorCenterAngle < -Math.PI) angle += Math.PI * 2;
  angle = clamp(angle, node.sectorMinAngle, node.sectorMaxAngle);

  const radius = Math.max(Math.hypot(node.px, node.py), node.minCollisionRadius || 0);
  node.angle = angle;
  node.radius = radius;
  [node.px, node.py] = pointRadial(node.angle, node.radius);
}

function resolveNodeCollisions(nodes) {
  const collidableNodes = nodes.filter((node) => node.depth > 0);

  for (let iteration = 0; iteration < 64; iteration += 1) {
    let moved = false;

    for (let i = 0; i < collidableNodes.length; i += 1) {
      for (let j = i + 1; j < collidableNodes.length; j += 1) {
        const a = collidableNodes[i];
        const b = collidableNodes[j];
        const minDistance = nodeCollisionRadius(a.data.type) + nodeCollisionRadius(b.data.type);
        let dx = b.px - a.px;
        let dy = b.py - a.py;
        let distance = Math.hypot(dx, dy);

        if (!distance) {
          dx = Math.cos(i + j);
          dy = Math.sin(i + j);
          distance = 1;
        }

        const overlap = minDistance - distance;
        if (overlap <= 0) continue;

        const aMovable = a.isCollisionMovable;
        const bMovable = b.isCollisionMovable;
        if (!aMovable && !bMovable) continue;

        const unitX = dx / distance;
        const unitY = dy / distance;
        const moveA = aMovable && bMovable ? overlap * 0.5 : (aMovable ? overlap : 0);
        const moveB = aMovable && bMovable ? overlap * 0.5 : (bMovable ? overlap : 0);

        if (moveA) {
          a.px -= unitX * moveA;
          a.py -= unitY * moveA;
          applyPolarConstraints(a);
          moved = true;
        }

        if (moveB) {
          b.px += unitX * moveB;
          b.py += unitY * moveB;
          applyPolarConstraints(b);
          moved = true;
        }
      }
    }

    if (!moved) break;
  }
}

function applyRadialTreeLayout(root, dimensions) {
  const anchors = root.children || [];
  const anchorCount = Math.max(anchors.length, 1);
  const minDimension = Math.min(dimensions.width, dimensions.height);
  const availableRadius = Math.max(180, minDimension / 2 - 22);
  const anchorRadius = Math.max(86, Math.min(148, availableRadius * 0.38));
  const outerNodeRadius = nodeSize('point').radius;
  const maxDepth = Math.max(1, max(root.descendants(), (node) => node.depth) || 1);
  const depthAfterAnchor = Math.max(maxDepth - 1, 1);
  const radialGap = Math.max(72, Math.min(132, (availableRadius - anchorRadius - outerNodeRadius) / depthAfterAnchor));
  const sectorSize = (Math.PI * 2) / anchorCount;

  root.angle = 0;
  root.radius = 0;
  root.px = 0;
  root.py = 0;
  root.isCollisionMovable = false;

  anchors.forEach((anchor, index) => {
    const centerAngle = index * sectorSize;
    const sectorPadding = Math.min(0.16, sectorSize * 0.12);
    const usableSector = Math.max(0.34, sectorSize - sectorPadding * 2);
    const leafCount = Math.max(1, anchor.leaves().length);
    const angleGap = Math.max(0.1, Math.min(0.36, usableSector / Math.max(leafCount - 0.5, 1)));

    tree()
      .nodeSize([angleGap, radialGap])
      .separation((a, b) => (a.parent === b.parent ? 1 : 1.2))(anchor);

    const anchorAngle = anchor.x;
    const anchorRadiusFromTree = anchor.y;
    const descendants = anchor.descendants();
    const minRelativeAngle = min(descendants, (node) => node.x - anchorAngle) ?? 0;
    const maxRelativeAngle = max(descendants, (node) => node.x - anchorAngle) ?? 0;
    const subtreeAngleSpan = Math.max(maxRelativeAngle - minRelativeAngle, 0.0001);
    const subtreeScale = Math.min(1, usableSector / subtreeAngleSpan);
    const minAngle = centerAngle - usableSector / 2;
    const maxAngle = centerAngle + usableSector / 2;

    descendants.forEach((node) => {
      const relativeAngle = (node.x - anchorAngle) * subtreeScale;
      node.angle = clamp(centerAngle + relativeAngle, minAngle, maxAngle);
      node.radius = anchorRadius + (node.y - anchorRadiusFromTree);
      node.sectorCenterAngle = centerAngle;
      node.sectorMinAngle = minAngle;
      node.sectorMaxAngle = maxAngle;
      node.minCollisionRadius = node.depth <= 1 ? anchorRadius : anchorRadius + radialGap * Math.max(node.depth - 1, 1);
      node.isCollisionMovable = node.depth > 1;
      [node.px, node.py] = pointRadial(node.angle, node.radius);
    });

    anchor.angle = centerAngle;
    anchor.radius = anchorRadius;
    anchor.sectorCenterAngle = centerAngle;
    anchor.sectorMinAngle = centerAngle;
    anchor.sectorMaxAngle = centerAngle;
    anchor.minCollisionRadius = anchorRadius;
    anchor.isCollisionMovable = false;
    [anchor.px, anchor.py] = pointRadial(anchor.angle, anchor.radius);
  });

  resolveNodeCollisions(root.descendants());
}

function transformFromPosition(node) {
  return `translate(${node.px},${node.py})`;
}

function labelClassForNode(node) {
  return `conversation-tree-node-label ${node.data.type}-label`;
}

function wrapText(textSelection) {
  textSelection.each(function wrapNodeLabel(node) {
    const textNode = select(this);
    const maxWidth = labelWidth(node.data.type);
    const maxLines = labelLineLimit(node.data.type);
    const lineHeight = 1.08;
    const characters = shortenLabel(node.data.name, labelMaxLength(node.data.type)).split('');
    const lines = [];
    let line = '';

    textNode.text(null);

    characters.forEach((character) => {
      const testLine = line + character;
      const measure = textNode.append('tspan').text(testLine);
      const tooWide = measure.node().getComputedTextLength() > maxWidth;
      measure.remove();

      if (tooWide && line) {
        lines.push(line);
        line = character;
      } else {
        line = testLine;
      }
    });

    if (line) lines.push(line);
    const visibleLines = lines.slice(0, maxLines);
    if (lines.length > maxLines && visibleLines.length) {
      visibleLines[visibleLines.length - 1] = shortenLabel(visibleLines[visibleLines.length - 1], 7);
    }

    const firstDy = -((visibleLines.length - 1) * lineHeight) / 2;
    visibleLines.forEach((visibleLine, index) => {
      textNode
        .append('tspan')
        .attr('x', 0)
        .attr('dy', `${index === 0 ? firstDy : lineHeight}em`)
        .text(visibleLine);
    });
  });
}

function appendNodeShape(nodeSelection) {
  nodeSelection.each(function appendShape(node) {
    const group = select(this);
    const size = nodeSize(node.data.type);

    if (nodeShape(node.data.type) === 'rect') {
      group
        .append('rect')
        .attr('class', 'conversation-tree-node-shape')
        .attr('x', -size.width / 2)
        .attr('y', -size.height / 2)
        .attr('width', size.width)
        .attr('height', size.height)
        .attr('rx', 11);
      return;
    }

    group
      .append('circle')
      .attr('class', 'conversation-tree-node-shape')
      .attr('r', size.radius);
  });
}

function statusText({ isActive, isLoading, isAnalyzing, analysisStatus, messageCount, mode }) {
  if (!isActive) {
    return mode === 'ai'
      ? { title: 'AI 對話開始後啟用', detail: '送出第一則訊息後，這裡會顯示你的個人想法脈絡樹。' }
      : { title: '真人配對後啟用', detail: '進入配對房間後，這裡會顯示你的個人想法脈絡樹。' };
  }

  if (isLoading) {
    return { title: '載入語意樹中', detail: '正在讀取目前房間的語意樹狀態。' };
  }

  if (analysisStatus === 'missing_gemini_api_key') {
    return { title: '語意分析未設定', detail: '目前無法自動整理想法脈絡，對話仍可正常進行。' };
  }

  if (isAnalyzing) {
    return { title: '正在整理脈絡', detail: '訊息已先送出，想法脈絡圖會在整理完成後更新。' };
  }

  if (!messageCount) {
    return { title: '等待對話訊息', detail: '送出訊息後會自動歸納到個人脈絡樹；未使用的大分類會先隱藏。' };
  }

  if (analysisStatus && analysisStatus !== 'ready') {
    return { title: '語意分析暫停', detail: '目前無法更新想法脈絡圖，對話仍可正常使用。' };
  }

  return null;
}

function ConversationTreePanel({
  topicTitle,
  treeData,
  trees = [],
  messageCount = 0,
  mode = 'matching',
  isActive,
  isLoading = false,
  isAnalyzing = false,
  analysisStatus = 'ready',
}) {
  const shellRef = useRef(null);
  const svgRef = useRef(null);
  const zoomTransformRef = useRef(null);
  const allTreeEntries = useMemo(
    () => normalizeTreeEntries(trees, treeData, topicTitle),
    [topicTitle, treeData, trees],
  );
  const treeEntries = useMemo(() => {
    if (mode !== 'matching') {
      return allTreeEntries;
    }

    const currentUserTrees = allTreeEntries.filter((entry) => entry.isCurrentUser);
    if (currentUserTrees.length) {
      return currentUserTrees;
    }

    return allTreeEntries.slice(0, 1).map((entry) => ({
      ...entry,
      label: '我的脈絡',
      isCurrentUser: true,
    }));
  }, [allTreeEntries, mode]);
  const preferredOwnerKey = treeEntries.find((entry) => entry.isCurrentUser)?.ownerKey || treeEntries[0]?.ownerKey || 'default';
  const [requestedOwnerKey, setRequestedOwnerKey] = useState('');
  const activeOwnerKey = treeEntries.some((entry) => entry.ownerKey === requestedOwnerKey)
    ? requestedOwnerKey
    : preferredOwnerKey;
  const activeTreeEntry = treeEntries.find((entry) => entry.ownerKey === activeOwnerKey)
    || treeEntries.find((entry) => entry.ownerKey === preferredOwnerKey)
    || treeEntries[0];
  const normalizedTreeData = activeTreeEntry?.treeData || normalizeTreeData(treeData, topicTitle);
  const visibleTreeData = useMemo(() => deriveVisibleTreeData(normalizedTreeData), [normalizedTreeData]);
  const [selectedNodeId, setSelectedNodeId] = useState(normalizedTreeData.id);
  const selectedNode = findNodeById(visibleTreeData, selectedNodeId) || visibleTreeData;
  const activeSelectedNodeId = selectedNode.id;
  const selectedPath = findNodePath(visibleTreeData, selectedNode.id) || [visibleTreeData];
  const selectedMessages = nodeMessages(selectedNode);
  const status = statusText({
    isActive,
    isLoading,
    isAnalyzing,
    analysisStatus,
    messageCount,
    mode,
  });

  useEffect(() => {
    const svgNode = svgRef.current;
    const shellNode = shellRef.current;
    if (!svgNode || !shellNode) {
      return undefined;
    }

    const svg = select(svgNode);
    const renderChart = () => {
      const bounds = shellNode.getBoundingClientRect();
      const width = Math.max(bounds.width, 340);
      const height = Math.max(bounds.height, 320);
      const root = hierarchy(visibleTreeData);

      svg.selectAll('*').remove();
      svg.attr('viewBox', `0 0 ${width} ${height}`);

      if (!isActive) {
        return;
      }

      applyRadialTreeLayout(root, { width, height });

      const centerX = width / 2;
      const centerY = height * (width < 520 ? 0.55 : 0.52);
      const viewportLayer = svg
        .append('g')
        .attr('transform', `translate(${centerX},${centerY})`);
      const zoomLayer = viewportLayer.append('g').attr('class', 'conversation-tree-zoom-layer');
      const radialLink = linkRadial()
        .angle((node) => node.angle)
        .radius((node) => node.radius);
      const transition = svg.transition().duration(420).ease(easeCubicOut);

      zoomLayer
        .append('g')
        .attr('class', 'conversation-tree-links')
        .selectAll('path')
        .data(root.links())
        .join('path')
        .attr('class', 'conversation-tree-link')
        .attr('d', radialLink)
        .attr('opacity', 0)
        .transition(transition)
        .attr('opacity', 1);

      const node = zoomLayer
        .append('g')
        .attr('class', 'conversation-tree-nodes')
        .selectAll('g')
        .data(root.descendants(), (item) => item.data.id)
        .join('g')
        .attr('class', (item) => `conversation-tree-node node-${item.data.type}`)
        .attr('transform', transformFromPosition)
        .attr('opacity', 0)
        .on('click', (event, item) => {
          event.stopPropagation();
          setSelectedNodeId(item.data.id);
        });

      appendNodeShape(node);

      node
        .append('text')
        .attr('class', labelClassForNode)
        .attr('x', 0)
        .attr('y', 0)
        .each(function renderLabel(item) {
          wrapText(select(this).datum(item));
        });

      node
        .append('title')
        .text((item) => {
          const messages = nodeMessages(item.data).map((message) => message.text).filter(Boolean);
          return [item.data.name, ...messages].join('\n');
        });

      node
        .transition(transition)
        .attr('opacity', 1);

      const zoomBehavior = zoom()
        .scaleExtent([0.58, 2.5])
        .on('zoom', (event) => {
          zoomTransformRef.current = event.transform;
          zoomLayer.attr('transform', event.transform);
        });

      svg
        .call(zoomBehavior)
        .on('dblclick.zoom', null)
        .on('click', () => setSelectedNodeId(visibleTreeData.id));

      if (zoomTransformRef.current) {
        svg.call(zoomBehavior.transform, zoomTransformRef.current);
      }
    };

    renderChart();

    const resizeObserver = new ResizeObserver(renderChart);
    resizeObserver.observe(shellNode);

    return () => {
      resizeObserver.disconnect();
      svg.on('.zoom', null);
      svg.on('click', null);
      svg.selectAll('*').remove();
    };
  }, [isActive, visibleTreeData]);

  useEffect(() => {
    const svgNode = svgRef.current;
    if (!svgNode) {
      return;
    }

    select(svgNode)
      .selectAll('.conversation-tree-node')
      .classed('is-selected', (item) => item.data.id === activeSelectedNodeId);
  }, [activeSelectedNodeId, visibleTreeData]);

  return (
    <section className="conversation-tree-panel" aria-label="核電語意對話樹">
      <div className="conversation-tree-header">
        <div>
          <span className="conversation-tree-eyebrow">想法脈絡圖</span>
          <h2>我的想法脈絡</h2>
        </div>
        <span className="conversation-tree-count">{messageCount} 則訊息</span>
      </div>

      {treeEntries.length > 1 && (
        <div className="conversation-tree-tabs" role="tablist" aria-label="語意脈絡切換">
          {treeEntries.map((entry) => (
            <button
              key={entry.ownerKey}
              type="button"
              role="tab"
              className={`conversation-tree-tab${entry.ownerKey === activeTreeEntry.ownerKey ? ' is-active' : ''}`}
              aria-selected={entry.ownerKey === activeTreeEntry.ownerKey}
              onClick={() => {
                setRequestedOwnerKey(entry.ownerKey);
                setSelectedNodeId(entry.treeData.id);
              }}
            >
              <span>{entry.label}</span>
              <small>{entry.analyzedSourceIds.length} 已分析</small>
            </button>
          ))}
        </div>
      )}

      <div ref={shellRef} className="conversation-tree-canvas-shell">
        <svg ref={svgRef} className="conversation-tree-canvas" role="img" aria-label="核電語意對話樹" />
        {status && (
          <div className="conversation-tree-empty">
            <strong>{status.title}</strong>
            <span>{status.detail}</span>
          </div>
        )}
      </div>

      <div className="conversation-tree-detail" aria-live="polite">
        <span className="conversation-tree-detail-label">
          {selectedPath.map((pathNode) => pathNode.name).join(' / ')}
        </span>
        {selectedMessages.length ? (
          <div className="conversation-tree-message-list">
            {selectedMessages.map((message, index) => {
              const confidence = Number(message.confidence);
              const confidenceText = Number.isFinite(confidence) ? `信心 ${(confidence * 100).toFixed(0)}%` : '';
              const timestamp = formatSemanticTime(message.sourceTimestamp || message.recordedAt);
              const meta = [message.stance || '中立', confidenceText, timestamp].filter(Boolean).join(' · ');

              return (
                <div className="conversation-tree-message" key={`${message.sourceMessageId || index}-${message.text}`}>
                  <p>{message.text || EMPTY_DETAIL_TEXT}</p>
                  {meta && <span>{meta}</span>}
                  {message.rationale && <small>{message.rationale}</small>}
                </div>
              );
            })}
          </div>
        ) : (
          <p>{selectedNode.type === 'anchor' ? `「${selectedNode.name}」目前還沒有相關訊息。` : EMPTY_DETAIL_TEXT}</p>
        )}
        <span className="conversation-tree-detail-time">滾輪可縮放，拖曳可平移</span>
      </div>
    </section>
  );
}

export default ConversationTreePanel;
