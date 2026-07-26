"use client";

import { useEffect, useState, useRef, useMemo } from "react";
import { api } from "@/lib/api";

interface GraphNode {
  id: string;
  category: string;
  industries: string[];
  hypotheses: string[];
  last_change: string;
  evidence: string;
  numeric_value: number | null;
  confidence_score?: number;
  backtest_status?: string;
  // Physics simulation coordinates
  x: number;
  y: number;
  vx: number;
  vy: number;
}

interface GraphLink {
  source: string;
  target: string;
  industry: string;
  hypothesis: string;
  evidence: string;
  confidence_score?: number;
  backtest_status?: string;
  backtest_label?: string;
  backtest_metrics?: {
    annual: number;
    sharpe: number;
    maxdd: number;
  } | null;
}

interface GraphData {
  nodes: GraphNode[];
  links: GraphLink[];
}

const CATEGORY_COLORS: Record<string, string> = {
  supply: "#2563EB",    // 蓝色
  demand: "#4F46E5",    // 靛青
  cost: "#D97706",      // 库金 (橘黄)
  price: "#0D9488",     // 松石 (松绿)
  capacity: "#7C3AED",  // 紫色
  margin: "#E11D48",    // 银朱 (红)
  earnings: "#16A34A",  // 绿色
  valuation: "#0891B2", // 青色
};

const CATEGORY_LABELS: Record<string, string> = {
  supply: "供给端",
  demand: "需求端",
  cost: "成本端",
  price: "价格端",
  capacity: "产能/效率",
  margin: "利润空间",
  earnings: "业绩释放",
  valuation: "估值中枢",
};

const CATEGORY_ABBR: Record<string, string> = {
  supply: "供",
  demand: "需",
  cost: "本",
  price: "价",
  capacity: "产",
  margin: "利",
  earnings: "绩",
  valuation: "估",
};

const INDUSTRY_COLORS: Record<string, string> = {
  "半导体": "#A855F7",  // purple
  "有色金属": "#F59E0B", // amber
  "白酒": "#EF4444",    // red
  "大消费": "#EF4444",   // red
  "AI算力": "#3B82F6",   // blue
  "商业航天": "#10B981", // emerald
};

const width = 850;
const height = 480;
const centerX = width / 2;
const centerY = height / 2;

export default function KnowledgeGraphView() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  
  // Graph states
  const [rawNodes, setRawNodes] = useState<any[]>([]);
  const [rawLinks, setRawLinks] = useState<GraphLink[]>([]);
  const [nodes, setNodes] = useState<GraphNode[]>([]);
  const [links, setLinks] = useState<GraphLink[]>([]);
  
  // Interactive states
  const [selectedIndustry, setSelectedIndustry] = useState<string>("all");
  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [hoveredLinkId, setHoveredLinkId] = useState<string | null>(null);
  
  // Zoom & Pan states
  const [zoom, setZoom] = useState(0.9);
  const [panX, setPanX] = useState(60);
  const [panY, setPanY] = useState(40);
  const [isPanning, setIsPanning] = useState(false);
  const panStartRef = useRef({ x: 0, y: 0 });
  
  // Dragging states
  const [draggedNodeId, setDraggedNodeId] = useState<string | null>(null);
  const dragOffsetRef = useRef({ x: 0, y: 0 });
  
  // Simulation physics parameters
  const simulationRef = useRef<number | null>(null);
  const [alpha, setAlpha] = useState(1.0);
  const svgRef = useRef<SVGSVGElement | null>(null);

  // 1. Fetch graph data
  useEffect(() => {
    setLoading(true);
    api.industryKnowledgeGraph()
      .then((data) => {
        if (data && Array.isArray(data.nodes) && data.nodes.length > 0) {
          setRawNodes(data.nodes);
          setRawLinks(data.links || []);
          initializePositions(data.nodes, data.links || []);
          setError(null);
        } else {
          setError("暂无真实的产业因果知识图谱数据。请在后台运行 `python3 scripts/ops/auto_download_reports.py` 抓取并解析真实研报。");
        }
        setLoading(false);
      })
      .catch((err) => {
        console.error("加载图谱失败:", err);
        setError("加载产业因果知识图谱失败。请确认后端服务已启动且图谱文件存在。");
        setLoading(false);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 2. Arrange nodes in a starting circle layout
  const initializePositions = (nodesList: any[], linksList: GraphLink[]) => {
    const initializedNodes = nodesList.map((n, idx) => {
      const angle = (idx / nodesList.length) * 2 * Math.PI;
      const radius = 160 + Math.random() * 20;
      return {
        ...n,
        x: centerX + Math.cos(angle) * radius,
        y: centerY + Math.sin(angle) * radius,
        vx: 0,
        vy: 0,
      } as GraphNode;
    });
    setNodes(initializedNodes);
    setLinks(linksList);
    setAlpha(1.0); // Reset energy
  };

  // 3. Extract unique list of industries for filtering
  const industriesList = useMemo(() => {
    const set = new Set<string>();
    rawNodes.forEach(n => {
      if (Array.isArray(n.industries)) {
        n.industries.forEach((ind: string) => set.add(ind));
      }
    });
    return Array.from(set);
  }, [rawNodes]);

  // 4. Filter nodes and links based on selected industry
  const filteredGraph = useMemo(() => {
    if (selectedIndustry === "all") {
      return { nodes, links };
    }
    
    // Keep nodes belonging to the selected industry
    const filteredNodes = nodes.filter(n => n.industries.includes(selectedIndustry));
    const activeIds = new Set(filteredNodes.map(n => n.id));
    
    // Keep links that connect two active nodes and match the industry
    const filteredLinks = links.filter(l => 
      activeIds.has(l.source) && activeIds.has(l.target) && l.industry === selectedIndustry
    );
    
    return { nodes: filteredNodes, links: filteredLinks };
  }, [nodes, links, selectedIndustry]);

  // 5. Lightweight Force-Directed Physics Engine Loop
  useEffect(() => {
    if (nodes.length === 0 || alpha < 0.005) {
      if (simulationRef.current) {
        cancelAnimationFrame(simulationRef.current);
        simulationRef.current = null;
      }
      return;
    }

    const step = () => {
      // Repulsion, Attraction, and Gravity physics constants
      const repulsionStrength = 8000;
      const attractionStrength = 0.04;
      const restLength = 110;
      const gravityStrength = 0.008;
      const friction = 0.85;

      const nodeMap = new Map<string, GraphNode>();
      nodes.forEach(n => nodeMap.set(n.id, n));

      // 1. Repulsion force between ALL pairs of nodes
      for (let i = 0; i < nodes.length; i++) {
        const nodeA = nodes[i];
        for (let j = i + 1; j < nodes.length; j++) {
          const nodeB = nodes[j];
          // Determine if they share an industry
          const shareIndustry = nodeA.industries.some(ind => nodeB.industries.includes(ind));
          const dx = nodeB.x - nodeA.x;
          const dy = nodeB.y - nodeA.y;
          const dist = Math.sqrt(dx * dx + dy * dy) || 1;
          
          // Nodes repel each other, with slightly stronger repulsion for unassociated nodes
          if (dist < 260) {
            const force = (repulsionStrength * (shareIndustry ? 1.0 : 1.2)) / (dist * dist);
            const fx = (dx / dist) * force;
            const fy = (dy / dist) * force;
            
            nodeA.vx -= fx;
            nodeA.vy -= fy;
            nodeB.vx += fx;
            nodeB.vy += fy;
          }
        }
      }

      // 2. Attraction force (spring) along links
      links.forEach(link => {
        const sourceNode = nodeMap.get(link.source);
        const targetNode = nodeMap.get(link.target);
        
        if (sourceNode && targetNode) {
          const dx = targetNode.x - sourceNode.x;
          const dy = targetNode.y - sourceNode.y;
          const dist = Math.sqrt(dx * dx + dy * dy) || 1;
          
          const force = attractionStrength * (dist - restLength);
          const fx = (dx / dist) * force;
          const fy = (dy / dist) * force;
          
          sourceNode.vx += fx;
          sourceNode.vy += fy;
          targetNode.vx -= fx;
          targetNode.vy -= fy;
        }
      });

      // 3. Central gravity (pull towards screen center)
      nodes.forEach(node => {
        const dx = centerX - node.x;
        const dy = centerY - node.y;
        
        node.vx += dx * gravityStrength;
        node.vy += dy * gravityStrength;
      });

      // 4. Update coordinates with damping, locking position of dragged node
      const updatedNodes = nodes.map(node => {
        if (node.id === draggedNodeId) {
          return node;
        }
        
        const nextX = Math.max(40, Math.min(width - 40, node.x + node.vx * alpha));
        const nextY = Math.max(40, Math.min(height - 40, node.y + node.vy * alpha));
        
        return {
          ...node,
          x: nextX,
          y: nextY,
          vx: node.vx * friction,
          vy: node.vy * friction,
        };
      });

      setNodes(updatedNodes);
      setAlpha(prev => prev * 0.985);
    };

    simulationRef.current = requestAnimationFrame(step);

    return () => {
      if (simulationRef.current) {
        cancelAnimationFrame(simulationRef.current);
      }
    };
  }, [nodes, links, alpha, draggedNodeId]);

  // 6. Interactive Drag & Drop Handlers
  const handleNodeMouseDown = (nodeId: string, event: React.MouseEvent) => {
    event.stopPropagation();
    event.preventDefault();
    setDraggedNodeId(nodeId);
    setSelectedNodeId(nodeId);
    setAlpha(1.0);

    const svg = svgRef.current;
    if (svg) {
      const rect = svg.getBoundingClientRect();
      const mouseX = (event.clientX - rect.left - panX) / zoom;
      const mouseY = (event.clientY - rect.top - panY) / zoom;
      
      const node = nodes.find(n => n.id === nodeId);
      if (node) {
        dragOffsetRef.current = {
          x: node.x - mouseX,
          y: node.y - mouseY,
        };
      }
    }
  };

  const handleMouseMove = (event: React.MouseEvent) => {
    if (draggedNodeId) {
      const svg = svgRef.current;
      if (svg) {
        const rect = svg.getBoundingClientRect();
        const mouseX = (event.clientX - rect.left - panX) / zoom;
        const mouseY = (event.clientY - rect.top - panY) / zoom;

        setNodes(prev => prev.map(n => {
          if (n.id === draggedNodeId) {
            return {
              ...n,
              x: mouseX + dragOffsetRef.current.x,
              y: mouseY + dragOffsetRef.current.y,
              vx: 0,
              vy: 0
            };
          }
          return n;
        }));
        setAlpha(1.0);
      }
    } else if (isPanning) {
      setPanX(prev => prev + (event.clientX - panStartRef.current.x));
      setPanY(prev => prev + (event.clientY - panStartRef.current.y));
      panStartRef.current = { x: event.clientX, y: event.clientY };
    }
  };

  const handleMouseUp = () => {
    setDraggedNodeId(null);
    setIsPanning(false);
  };

  const handleSvgMouseDown = (event: React.MouseEvent) => {
    setIsPanning(true);
    panStartRef.current = { x: event.clientX, y: event.clientY };
  };

  const handleWheel = (event: React.WheelEvent) => {
    event.preventDefault();
    const scaleFactor = 1.08;
    const nextZoom = event.deltaY < 0 ? zoom * scaleFactor : zoom / scaleFactor;
    const boundedZoom = Math.max(0.35, Math.min(2.5, nextZoom));
    
    const svg = svgRef.current;
    if (svg) {
      const rect = svg.getBoundingClientRect();
      const mouseX = event.clientX - rect.left;
      const mouseY = event.clientY - rect.top;
      
      setPanX(prev => mouseX - (mouseX - prev) * (boundedZoom / zoom));
      setPanY(prev => mouseY - (mouseY - prev) * (boundedZoom / zoom));
    }
    
    setZoom(boundedZoom);
  };

  const zoomIn = () => {
    setZoom(prev => Math.min(2.5, prev * 1.15));
  };
  const zoomOut = () => {
    setZoom(prev => Math.max(0.35, prev / 1.15));
  };
  const resetViewport = () => {
    setZoom(0.9);
    setPanX(60);
    setPanY(40);
  };

  const resetLayout = () => {
    initializePositions(rawNodes, rawLinks);
  };

  // Highlight connections on hover
  const getHighlightDetails = () => {
    const connectedNodeIds = new Set<string>();
    const activeLinkIndices = new Set<number>();

    if (hoveredNodeId) {
      connectedNodeIds.add(hoveredNodeId);
      filteredGraph.links.forEach((l, idx) => {
        if (l.source === hoveredNodeId) {
          connectedNodeIds.add(l.target);
          activeLinkIndices.add(idx);
        } else if (l.target === hoveredNodeId) {
          connectedNodeIds.add(l.source);
          activeLinkIndices.add(idx);
        }
      });
    }

    return { connectedNodeIds, activeLinkIndices };
  };

  const { connectedNodeIds, activeLinkIndices } = getHighlightDetails();

  const focusedNode = useMemo(() => {
    const targetId = hoveredNodeId || selectedNodeId;
    return filteredGraph.nodes.find(n => n.id === targetId) || null;
  }, [hoveredNodeId, selectedNodeId, filteredGraph.nodes]);

  const focusedLink = useMemo(() => {
    if (hoveredLinkId) {
      const [src, tgt] = hoveredLinkId.split("->");
      return filteredGraph.links.find(l => l.source === src && l.target === tgt) || null;
    }
    return null;
  }, [hoveredLinkId, filteredGraph.links]);

  if (loading) {
    return <div className="card text-center py-12 text-subink bg-[#1E2E42]/80 border border-cardline rounded-lg">正在加载产业拓扑结构图谱数据...</div>;
  }

  if (error) {
    return (
      <div className="card text-center py-12 bg-[#1E2E42]/80 border border-cardline rounded-lg space-y-3">
        <div className="text-[14px] text-subink font-medium">⚠️ {error}</div>
        <div className="text-[12px] text-subink/80 max-w-[500px] mx-auto leading-relaxed">
          本系统为严谨的量化研究平台，仅展示从实际卖方研报 PDF 中经 LLM 抽取并经过交易日历对齐的真实逻辑链条，不使用任何 Mock 或测试数据进行展示污染。
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* 1. Control & Filter Panel */}
      <div className="flex flex-wrap items-center justify-between gap-3 bg-[#243752]/70 p-3 rounded-lg border border-cardline">
        <div className="flex items-center gap-2">
          <span className="text-[13px] text-subink font-medium">行业过滤器:</span>
          <div className="flex gap-1.5">
            <button
              onClick={() => { setSelectedIndustry("all"); setAlpha(1.0); }}
              className={`text-[12px] px-2.5 py-1 rounded transition ${
                selectedIndustry === "all" ? "bg-brand text-white border border-brand" : "bg-bg/40 text-subink border border-cardline hover:text-ink"
              }`}
            >
              全市场
            </button>
            {industriesList.map(ind => (
              <button
                key={ind}
                onClick={() => { setSelectedIndustry(ind); setAlpha(1.0); }}
                className={`text-[12px] px-2.5 py-1 rounded border transition ${
                  selectedIndustry === ind 
                    ? "text-white font-medium" 
                    : "bg-bg/40 text-subink border-cardline hover:text-ink"
                }`}
                style={{
                  backgroundColor: selectedIndustry === ind ? INDUSTRY_COLORS[ind] || "#3B82F6" : undefined,
                  borderColor: selectedIndustry === ind ? INDUSTRY_COLORS[ind] || "#3B82F6" : undefined,
                }}
              >
                {ind}
              </button>
            ))}
          </div>
        </div>

        {/* Action Controls */}
        <div className="flex items-center gap-1.5">
          <button onClick={zoomIn} title="放大" className="btn bg-bg border border-line text-[13px] px-2.5 py-1 hover:text-ink">＋</button>
          <button onClick={zoomOut} title="缩小" className="btn bg-bg border border-line text-[13px] px-2.5 py-1 hover:text-ink">－</button>
          <button onClick={resetViewport} title="重置视角" className="btn bg-bg border border-line text-[12px] px-2 py-1 hover:text-ink">重设中心</button>
          <button onClick={resetLayout} title="重新布局" className="btn bg-brand/10 border border-brand/35 text-[12px] px-2 py-1 text-brand hover:bg-brand/20">重新模拟</button>
        </div>
      </div>

      {/* 2. SVG Visualization Canvas */}
      <div className="relative border border-line bg-[#FCFAF5]/50 rounded-xl shadow-inner overflow-hidden select-none" style={{ height: `${height}px` }}>
        {/* Grid pattern */}
        <div className="absolute inset-0 bg-[radial-gradient(#DFD7C2_1.5px,transparent_1.5px)] [background-size:20px_20px] opacity-35 pointer-events-none" />

        <svg
          ref={svgRef}
          width="100%"
          height="100%"
          viewBox={`0 0 ${width} ${height}`}
          onMouseMove={handleMouseMove}
          onMouseUp={handleMouseUp}
          onMouseLeave={handleMouseUp}
          onMouseDown={handleSvgMouseDown}
          onWheel={handleWheel}
          className="cursor-grab active:cursor-grabbing"
          style={{ transition: isPanning ? "none" : "transform 0.1s ease-out" }}
        >
          <defs>
            <marker id="arrow" viewBox="0 0 10 10" refX="28" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z" fill="#4B5563" />
            </marker>
            <marker id="arrow-highlight" viewBox="0 0 10 10" refX="28" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z" fill="#60A5FA" />
            </marker>
            <marker id="arrow-semiconductor" viewBox="0 0 10 10" refX="28" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z" fill="#A855F7" />
            </marker>
            <marker id="arrow-metals" viewBox="0 0 10 10" refX="28" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z" fill="#F59E0B" />
            </marker>
            
            <filter id="glow" x="-20%" y="-20%" width="140%" height="140%">
              <feGaussianBlur stdDeviation="3" result="blur" />
              <feComposite in="SourceGraphic" in2="blur" operator="over" />
            </filter>
          </defs>

          {/* Canvas Translate & Scale */}
          <g transform={`translate(${panX}, ${panY}) scale(${zoom})`}>
            
            {/* Draw Links (Edges) */}
            {filteredGraph.links.map((link, idx) => {
              const sourceNode = nodes.find(n => n.id === link.source);
              const targetNode = nodes.find(n => n.id === link.target);
              if (!sourceNode || !targetNode) return null;

              const isLinkHovered = hoveredLinkId === `${link.source}->${link.target}`;
              const isDirectlyHighlighted = hoveredNodeId 
                ? (link.source === hoveredNodeId || link.target === hoveredNodeId)
                : false;
              const isDimmed = hoveredNodeId ? !isDirectlyHighlighted : false;

              let strokeColor = "#4B5563";
              let markerEnd = "url(#arrow)";
              
              if (isDirectlyHighlighted || isLinkHovered) {
                strokeColor = INDUSTRY_COLORS[link.industry] || "#60A5FA";
                markerEnd = link.industry === "半导体" ? "url(#arrow-semiconductor)" : link.industry === "有色金属" ? "url(#arrow-metals)" : "url(#arrow-highlight)";
              }

              const isFlowing = isDirectlyHighlighted || isLinkHovered;

              return (
                <g key={`link-${idx}`}>
                  <line
                    x1={sourceNode.x}
                    y1={sourceNode.y}
                    x2={targetNode.x}
                    y2={targetNode.y}
                    stroke="transparent"
                    strokeWidth="12"
                    className="cursor-pointer"
                    onMouseEnter={() => setHoveredLinkId(`${link.source}->${link.target}`)}
                    onMouseLeave={() => setHoveredLinkId(null)}
                  />
                  <line
                    x1={sourceNode.x}
                    y1={sourceNode.y}
                    x2={targetNode.x}
                    y2={targetNode.y}
                    stroke={strokeColor}
                    strokeWidth={isFlowing ? "2.5" : "1.5"}
                    strokeOpacity={isDimmed ? 0.15 : isFlowing ? 1.0 : 0.45}
                    markerEnd={markerEnd}
                    className={isFlowing ? "flow-line" : ""}
                    style={{ transition: "stroke 0.2s, stroke-width 0.2s" }}
                  />
                  {isFlowing && (
                    <line
                      x1={sourceNode.x}
                      y1={sourceNode.y}
                      x2={targetNode.x}
                      y2={targetNode.y}
                      stroke={strokeColor}
                      strokeWidth="2.5"
                      strokeOpacity="0.8"
                      strokeDasharray="6, 8"
                      className="flow-line-anim"
                    />
                  )}
                </g>
              );
            })}

            {/* Draw Nodes */}
            {filteredGraph.nodes.map((node) => {
              const isHovered = hoveredNodeId === node.id;
              const isSelected = selectedNodeId === node.id;
              const isDimmed = hoveredNodeId ? !connectedNodeIds.has(node.id) : false;
              
              const catColor = CATEGORY_COLORS[node.category] || "#9CA3AF";
              const abbr = CATEGORY_ABBR[node.category] || "●";
              const isOverlap = node.industries.length > 1;

              return (
                <g
                  key={node.id}
                  transform={`translate(${node.x}, ${node.y})`}
                  className="cursor-grab active:cursor-grabbing select-none"
                  onMouseDown={(e) => handleNodeMouseDown(node.id, e)}
                  onMouseEnter={() => setHoveredNodeId(node.id)}
                  onMouseLeave={() => setHoveredNodeId(null)}
                >
                  {(isHovered || isSelected) && (
                    <circle
                      r="32"
                      fill={catColor}
                      fillOpacity="0.12"
                      filter="url(#glow)"
                    />
                  )}

                  <circle
                    r="24"
                    fill="#FFFFFF"
                    stroke={catColor}
                    strokeWidth={isHovered ? "3" : "2"}
                    strokeOpacity={isDimmed ? 0.25 : 1.0}
                    style={{ transition: "stroke-width 0.15s, stroke-opacity 0.15s" }}
                  />

                  {isOverlap && (
                    <circle
                      r="28"
                      fill="none"
                      stroke="#A855F7"
                      strokeWidth="1.5"
                      strokeDasharray="4, 3"
                      strokeOpacity={isDimmed ? 0.2 : 0.8}
                    />
                  )}

                  <text
                    dy="5"
                    textAnchor="middle"
                    fill={catColor}
                    fillOpacity={isDimmed ? 0.25 : 1.0}
                    fontSize="13px"
                    fontWeight="bold"
                    className="pointer-events-none"
                  >
                    {abbr}
                  </text>

                  {/* Label tag */}
                  <g transform="translate(0, 38)">
                    <rect
                      x={-(node.id.length * 6) - 10}
                      y="-12"
                      width={(node.id.length * 12) + 20}
                      height="20"
                      rx="4"
                      fill="#FFFFFF"
                      fillOpacity={isDimmed ? 0.45 : 0.95}
                      stroke={isHovered ? catColor : "rgba(223, 215, 194, 0.6)"}
                      strokeWidth="1"
                    />
                    <text
                      textAnchor="middle"
                      fill={isHovered ? "#CC5D20" : isDimmed ? "#A7AAA1" : "#31322C"}
                      fontSize="11.5px"
                      fontWeight={isHovered ? "bold" : "normal"}
                      className="pointer-events-none"
                    >
                      {node.id}
                    </text>
                  </g>
                </g>
              );
            })}
            
          </g>
        </svg>

        {/* Hover Link Details Overlay */}
        {focusedLink && (
          <div className="absolute top-3 right-3 max-w-[320px] bg-white/95 border border-line p-3 rounded-lg shadow-xl text-[12px] space-y-1.5 transition-all">
            <div className="flex items-center justify-between">
              <span className="font-bold text-brand uppercase tracking-wider">因果传导关系</span>
              <span className="text-[10px] bg-bg px-2 py-0.5 rounded text-brand border border-line/60">
                {focusedLink.industry}
              </span>
            </div>
            <div className="text-ink font-semibold flex items-center gap-1">
              <span>{focusedLink.source}</span>
              <span className="text-subink text-[11px]">→</span>
              <span>{focusedLink.target}</span>
            </div>
            <div className="text-[11px] text-subink italic bg-bg/85 p-1.5 rounded border border-line/45 leading-relaxed">
              “ {focusedLink.evidence} ”
            </div>
            {focusedLink.backtest_status && (
              <div className="flex items-center gap-1.5 text-[10.5px]">
                <span className="text-subink">回测绩效:</span>
                <span className={`px-1.5 py-0.5 rounded-sm text-[9.5px] font-semibold border ${
                  focusedLink.backtest_status === "verified" ? "text-songshi bg-songshi/10 border-songshi/20" :
                  focusedLink.backtest_status === "refuted" ? "text-yinzhu bg-yinzhu/10 border-yinzhu/20" :
                  focusedLink.backtest_status === "weak" ? "text-warn bg-warn/10 border-warn/20" :
                  "text-subink bg-bg border border-line/40"
                }`}>
                  {focusedLink.backtest_label}
                </span>
                {focusedLink.backtest_metrics && (
                  <span className="font-mono text-brand font-semibold">
                    S:{focusedLink.backtest_metrics.sharpe.toFixed(1)} / R:{(focusedLink.backtest_metrics.annual * 100).toFixed(0)}%
                  </span>
                )}
              </div>
            )}
            <div className="text-[10px] text-subink/70 font-mono text-right">
              源自：{focusedLink.hypothesis}
            </div>
          </div>
        )}

        {/* Color Legend */}
        <div className="absolute bottom-3 left-3 flex flex-wrap gap-x-3 gap-y-1 bg-white/90 border border-line px-2.5 py-1.5 rounded-lg max-w-[320px] text-[10px]">
          {Object.entries(CATEGORY_LABELS).map(([cat, label]) => (
            <div key={cat} className="flex items-center gap-1">
              <span className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: CATEGORY_COLORS[cat] }} />
              <span className="text-subink">{label}</span>
            </div>
          ))}
          <div className="flex items-center gap-1 border-l border-line/60 pl-2">
            <span className="w-2.5 h-2.5 rounded-full border border-[#A855F7] border-dashed" />
            <span className="text-subink">多产业交汇节点</span>
          </div>
        </div>
      </div>

      {/* 3. Detailed Information Sticky Panel (Bottom Details) */}
      <div className="card border border-line bg-white p-4 rounded-xl shadow-sm">
        {focusedNode ? (
          <div className="space-y-2.5">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line/50 pb-2">
              <div className="flex items-center gap-2">
                <span 
                  className="w-3.5 h-3.5 rounded-full" 
                  style={{ backgroundColor: CATEGORY_COLORS[focusedNode.category] }} 
                />
                <h4 className="text-[15px] font-bold text-ink">
                  {focusedNode.id}
                </h4>
                <span className="text-[11px] text-subink font-medium px-2 py-0.5 bg-bg/80 rounded border border-line">
                  {CATEGORY_LABELS[focusedNode.category]}
                </span>
              </div>
              <div className="flex gap-1">
                {focusedNode.industries.map(ind => (
                  <span 
                    key={ind} 
                    className="text-[10px] text-white px-2 py-0.5 rounded font-semibold"
                    style={{ backgroundColor: INDUSTRY_COLORS[ind] || "#3B82F6" }}
                  >
                    {ind}
                  </span>
                ))}
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
              <div className="space-y-1">
                <div className="text-[11px] text-subink">最新指标值</div>
                <div className="text-brand font-mono text-[14px] font-semibold">
                  {focusedNode.numeric_value !== null ? focusedNode.numeric_value : "— (未提及数值)"}
                </div>
              </div>
              <div className="space-y-1">
                <div className="text-[11px] text-subink">传导状态</div>
                <div className="text-[13px] font-bold">
                  {focusedNode.last_change === "up" && <span className="text-songshi">▲ 趋势上行 (Up)</span>}
                  {focusedNode.last_change === "down" && <span className="text-yinzhu">▼ 趋势下行 (Down)</span>}
                  {focusedNode.last_change === "stable" && <span className="text-subink">● 稳定平持 (Stable)</span>}
                  {focusedNode.last_change === "volatile" && <span className="text-warn">✦ 剧烈波动 (Volatile)</span>}
                  {!focusedNode.last_change && <span className="text-subink">— (未指定)</span>}
                </div>
              </div>
              <div className="space-y-1">
                <div className="text-[11px] text-subink">回测置信度评分</div>
                <div className="flex items-center gap-1.5 mt-0.5">
                  <span className="text-[14px] font-mono font-bold text-warn">
                    {focusedNode.confidence_score !== undefined ? `${(focusedNode.confidence_score * 100).toFixed(0)}%` : "50%"}
                  </span>
                  <span className={`text-[10px] px-1.5 py-0.5 rounded font-semibold border ${
                    focusedNode.backtest_status === "verified" ? "bg-songshi/10 text-songshi border-songshi/30" :
                    focusedNode.backtest_status === "refuted" ? "bg-yinzhu/10 text-yinzhu border-yinzhu/30" :
                    focusedNode.backtest_status === "weak" ? "bg-warn/10 text-warn border-warn/30" :
                    "bg-bg/80 text-subink border-line"
                  }`}>
                    {focusedNode.backtest_status === "verified" ? "已获实证" :
                     focusedNode.backtest_status === "refuted" ? "回测证伪" :
                     focusedNode.backtest_status === "weak" ? "弱信号" : "待验证"}
                  </span>
                </div>
              </div>
              <div className="space-y-1">
                <div className="text-[11px] text-subink">关联的研究假设</div>
                <div className="flex flex-wrap gap-1">
                  {focusedNode.hypotheses.map(hyp => (
                    <code key={hyp} className="text-[10.5px] font-mono bg-bg px-1.5 py-0.5 rounded text-subink border border-line/60">
                      {hyp}
                    </code>
                  ))}
                </div>
              </div>
            </div>

            <div className="bg-bg/60 p-2.5 rounded border border-line/60 text-[12px] leading-relaxed">
              <div className="text-[10px] text-brand font-semibold mb-1 uppercase tracking-wider">最新研报提取原证证据:</div>
              <div className="text-ink italic">“ {focusedNode.evidence || "暂无此节点证据原文描述。"} ”</div>
            </div>
          </div>
        ) : (
          <div className="text-center py-6 text-subink text-[13px]">
            💡 请在上方拓扑图中悬停或点击节点，查看其产业传导机制及研报原文证据
          </div>
        )}
      </div>

      <style jsx global>{`
        @keyframes flow {
          to {
            stroke-dashoffset: -20;
          }
        }
        .flow-line {
          stroke-dasharray: 5, 5;
          animation: flow 1s linear infinite;
        }
        .flow-line-anim {
          animation: flow 0.8s linear infinite;
        }
      `}</style>
    </div>
  );
}
