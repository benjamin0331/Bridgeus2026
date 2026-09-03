"""產生 BridgeUs 系統架構圖（Diagrams + Graphviz，含各工具 logo）。

用法：
    uv run -p 3.13 --with diagrams --with svglib --with rlPyCairo scripts/gen_arch_diagram.py
需先安裝 Graphviz（winget install Graphviz.Graphviz）。
輸出：docs/architecture.png
"""
import io
import os
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ICON_DIR = Path(__file__).resolve().parent / "diagram_icons"
OUT = ROOT / "docs" / "architecture"

# ponytail: Graphviz 裝在預設路徑就自動補 PATH，不用每個人去改環境變數
_GV = Path(r"C:\Program Files\Graphviz\bin")
if _GV.exists():
    os.environ["PATH"] = f"{_GV};{os.environ['PATH']}"

# Diagrams 內建沒有的 logo，從 Iconify 抓（logos/* 是彩色官方標誌）
ICONS = {
    "d3": "simple-icons/d3dotjs?color=%23F9A03C",
    "vite": "simple-icons/vite?color=%23646CFF",
    "godot": "logos/godot-icon",
    "chroma": "logos/chroma",
    "openai": "logos/openai-icon",
    "hf": "logos/hugging-face-icon",
    "pytorch": "logos/pytorch-icon",
    "anthropic": "simple-icons/anthropic?color=%23D97757",
    "langchain": "simple-icons/langchain?color=%231C3C3C",
    "uv": "simple-icons/uv?color=%23DE5FE9",
    "pytest": "simple-icons/pytest?color=%230A9EDC",
}


def icon(key: str) -> str:
    """回傳 logo 的本地 PNG 路徑，沒有就抓下來轉檔（Graphviz 不吃 SVG）。"""
    png = ICON_DIR / f"{key}.png"
    if not png.exists():
        from reportlab.graphics import renderPM
        from svglib.svglib import svg2rlg

        ICON_DIR.mkdir(exist_ok=True)
        path, _, query = ICONS[key].partition("?")
        url = f"https://api.iconify.design/{path}.svg?height=256" + (f"&{query}" if query else "")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        svg = urllib.request.urlopen(req).read()
        drawing = svg2rlg(io.BytesIO(svg))
        renderPM.drawToFile(drawing, str(png), fmt="PNG", bg=0xFFFFFF)
    return str(png)


from diagrams import Cluster, Diagram, Edge  # noqa: E402
from diagrams.custom import Custom  # noqa: E402
from diagrams.onprem.client import Users  # noqa: E402
from diagrams.onprem.container import Docker  # noqa: E402
from diagrams.onprem.vcs import Git  # noqa: E402
from diagrams.onprem.database import PostgreSQL  # noqa: E402
from diagrams.onprem.inmemory import Redis  # noqa: E402
from diagrams.onprem.network import Nginx  # noqa: E402
from diagrams.programming.framework import Django, React  # noqa: E402
from diagrams.saas.cdn import Cloudflare  # noqa: E402

FONT = "Microsoft JhengHei"
graph_attr = {"fontname": FONT, "fontsize": "20", "pad": "0.4", "splines": "spline", "nodesep": "0.6", "ranksep": "1.2"}
node_attr = {"fontname": FONT, "fontsize": "11"}
edge_attr = {"fontname": FONT, "fontsize": "10", "color": "#666666"}
BLUE = {"bgcolor": "#EAF3FB", "pencolor": "#9DC3E6", "fontname": FONT, "fontsize": "13"}
GREEN = {"bgcolor": "#EAF6EC", "pencolor": "#9CCCA9", "fontname": FONT, "fontsize": "13"}
AMBER = {"bgcolor": "#FBF3E4", "pencolor": "#DCC58A", "fontname": FONT, "fontsize": "13"}
GRAY = {"bgcolor": "#F1F1F1", "pencolor": "#BBBBBB", "fontname": FONT, "fontsize": "13"}

with Diagram(
    "",
    filename=str(OUT),
    outformat="png",
    show=False,
    direction="LR",
    graph_attr=graph_attr,
    node_attr=node_attr,
    edge_attr=edge_attr,
):
    users = Users("使用者\n受試者 / 研究者")

    with Cluster("瀏覽器 Client", graph_attr=GREEN):
        spa = React("React 19 + Router 7\nAxios")
        ccnd = Custom("D3.js 7.9\nCCND 放射樹", icon("d3"))
        godot_web = Custom("Godot 4.7\n虛擬互動空間", icon("godot"))
        vite = Custom("Vite 8\n打包建置", icon("vite"))
        vite >> Edge(style="dashed", label="build") >> spa
        spa >> Edge(color="#9CCCA9") >> ccnd
        spa >> Edge(color="#9CCCA9") >> godot_web

    with Cluster("開發 / 部署", graph_attr=GRAY):
        docker = Docker("Docker Compose\n容器編排")
        uv_ = Custom("uv\nPython 套件與環境", icon("uv"))
        git = Git("Git\n版本控制")
        pytest_ = Custom("pytest\n後端測試", icon("pytest"))
        # ponytail: invis 串成一列，工具框才會橫躺、不被拉成一長條
        git >> Edge(style="invis") >> uv_ >> Edge(style="invis") >> pytest_ >> Edge(style="invis") >> docker

    tunnel = Cloudflare("Cloudflare Tunnel\nTLS 終端")
    proxy = Nginx("nginx\n反向代理")

    with Cluster("應用伺服器（Docker Compose / VPS）", graph_attr=BLUE):
        with Cluster("Django 6 ASGI（uvicorn）", graph_attr=BLUE):
            api = Django("DRF 3.17 REST API\nM1 認證 · M2 立場 · M3 配對 · M6 摘要")
            ws = Django("Channels 4.3 WebSocket\nM4 對話室 · M5 CCND 推送")
        godot_srv = Custom("Godot headless server\nsystemd 常駐", icon("godot"))

    with Cluster("資料層", graph_attr=BLUE):
        pg = PostgreSQL("PostgreSQL 16 + pgvector\n帳號 · 對話 · 語意向量")
        redis = Redis("Redis 7\ncache + channel layer")
        chroma = Custom("ChromaDB 1.5\nRAG 外部知識庫", icon("chroma"))

    with Cluster("AI / NLP", graph_attr=AMBER):
        lc = Custom("LangChain\nRAG 管線", icon("langchain"))
        claude = Custom("Claude Sonnet 4.6\nAI 對話 · 摘要", icon("anthropic"))
        oai = Custom("OpenAI gpt-5.4-mini\nCCND 節點分類退路", icon("openai"))
        st = Custom("Sentence-Transformers\n多語 embedding", icon("hf"))
        bert = Custom("BERT / DistilBERT\n節點分類 · 情緒強度", icon("hf"))
        torch = Custom("PyTorch + Transformers\n本地推論", icon("pytorch"))
        [st, bert] >> Edge(style="dashed", color="#DCC58A") >> torch

    users >> Edge(label="HTTPS / WSS") >> spa >> tunnel >> proxy
    proxy >> Edge(label="/api") >> api
    proxy >> Edge(label="/ws") >> ws
    proxy >> Edge(label="/godot-ws（iframe 連線）", style="dashed") >> godot_srv
    docker >> Edge(style="dashed", color="#BBBBBB", label="部署") >> api

    # ponytail: invis 邊把 AI 層排在資料層右邊，兩個框才能並排；
    # 否則 dot 會把它們塞進同一欄、上下疊成一長條，圖就變得又高又空
    for _ai in (lc, oai, st, bert):
        redis >> Edge(style="invis") >> _ai

    api >> Edge(label="RAG 檢索") >> lc
    api >> Edge(label="節點分類") >> oai
    api >> Edge(label="立場向量") >> st
    ws >> Edge(label="情緒 / 離題偵測") >> bert
    lc >> claude
    lc >> Edge(constraint="false") >> chroma
    ws >> Edge(label="streaming 回覆") >> claude
    st >> Edge(style="dashed", label="384 維") >> pg
    [api, ws] >> Edge(color="#4A7EBB") >> pg
    [api, ws] >> Edge(color="#4A7EBB") >> redis

print("wrote", OUT.with_suffix(".png"))
