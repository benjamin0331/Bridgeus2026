# 常駐 Godot headless 多人連線 server（backend/Dockerfile 走 Django，
# 這支只跑 Godot 自己的 WebSocketMultiplayerPeer server，port 8085）。
# 不含 Web 匯出——那是靜態檔，走 frontend/nginx，見 frontend/Dockerfile。

FROM debian:bookworm-slim AS godot-binary

ARG GODOT_VERSION=4.7.1-stable
ARG GODOT_ARCHIVE=Godot_v${GODOT_VERSION}_linux.x86_64

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl unzip \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fL -o /tmp/godot.zip \
       "https://github.com/godotengine/godot/releases/download/${GODOT_VERSION}/${GODOT_ARCHIVE}.zip" \
    && unzip /tmp/godot.zip -d /tmp \
    && mv "/tmp/${GODOT_ARCHIVE}" /usr/local/bin/godot \
    && chmod +x /usr/local/bin/godot \
    && rm /tmp/godot.zip


FROM debian:bookworm-slim

# 這是 Godot 官方的一般 Linux 執行檔（非額外匯出的「Server」精簡版），即使用
# --headless 執行，動態連結還是會找 X11/GL/音效/udev 這些函式庫，沒裝的話
# 啟動時是 "error while loading shared libraries: libXXX.so: cannot open
# shared object file"。這份清單是已知需要的，若實際 build 時仍缺，照錯誤訊息
# 缺什麼裝什麼、重新 build 即可。
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       libx11-6 libxcursor1 libxinerama1 libxrandr2 libxi6 libxext6 \
       libgl1 libglu1-mesa \
       libasound2 libpulse0 \
       libudev1 libdbus-1-3 \
       ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=godot-binary /usr/local/bin/godot /usr/local/bin/godot

WORKDIR /app
COPY . .

# build time 先匯入一次，把 .godot/（字型資料、場景 UID 對照）烤進 image：
# 一是讓 container 開機不用等 import，二是讓匯入失敗在 build 階段就爆出來，
# 不是等部署上線才發現。實測過（本機、目標伺服器）第一次 import 常因為
# 字型等資源的 .import 中介檔案還沒生成而報錯，第二次才會完全乾淨，所以這裡
# 跑兩次；即使某次非零結束也不中斷 build（Godot 的 --import 在乾淨情況下
# 仍偶爾回非零 exit code，這是已知的 CLI 行為，不代表匯入失敗)。
RUN godot --headless --path /app --import; \
    godot --headless --path /app --import; \
    true

EXPOSE 8085

# GODOT_SERVICE_TOKEN 由 docker-compose 的 env_file 帶入（見 docker-compose.yml），
# 這裡不設預設值——沒設定時 Backend.gd 讀到空字串，request_topic_match 會直接
# 略過並印警告，不會靜默用錯密鑰。
ENTRYPOINT ["godot", "--headless", "--path", "/app"]
CMD ["--", "--server"]
