"""Range-aware 檔案伺服器，服務 MEDIA_URL 底下的上傳檔（見 urls.py）。

DEBUG 與正式站都會走這裡：影片的 url 存的是根相對路徑（/media/...），正式站
由前面的反向代理把 /media/ 原封不動轉到這個後端，所以這條路由不能只在開發
模式掛。

django.views.static.serve() 完全不理會 Range header（原始碼裡沒有任何相關
邏輯），每次都回傳整個檔案；瀏覽器原生 <video> 播放器因此沒辦法拖曳時間軸，
Chrome 在拿不到 Range 時甚至常常直接放棄解析音軌，實際觀察到的症狀就是
「有畫面沒聲音、不能拖時間軸」。正式環境如果換成 nginx / S3 之類的物件
儲存，這支視圖就不需要了——那些本來就有正確的 Range 支援。
"""

import mimetypes
import re
from pathlib import Path

from django.http import (
    FileResponse,
    Http404,
    HttpResponse,
    HttpResponseNotModified,
    StreamingHttpResponse,
)
from django.utils._os import safe_join
from django.utils.http import http_date
from django.views.static import was_modified_since

RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


def _read_range(fileobj, start, length, block_size=8192):
    fileobj.seek(start)
    remaining = length
    while remaining > 0:
        chunk = fileobj.read(min(block_size, remaining))
        if not chunk:
            break
        remaining -= len(chunk)
        yield chunk
    fileobj.close()


def serve_media(request, path, document_root=None):
    clean_path = path.lstrip("/")
    fullpath = Path(safe_join(document_root, clean_path))
    if not fullpath.exists() or fullpath.is_dir():
        raise Http404("找不到這個檔案。")

    statobj = fullpath.stat()
    if not was_modified_since(
        request.META.get("HTTP_IF_MODIFIED_SINCE"), statobj.st_mtime
    ):
        return HttpResponseNotModified()

    content_type, encoding = mimetypes.guess_type(str(fullpath))
    content_type = content_type or "application/octet-stream"
    file_size = statobj.st_size

    range_match = RANGE_RE.match(request.META.get("HTTP_RANGE", ""))
    if range_match:
        start_str, end_str = range_match.groups()
        start = int(start_str) if start_str else 0
        end = int(end_str) if end_str else file_size - 1
        end = min(end, file_size - 1)

        if start > end or start >= file_size:
            response = HttpResponse(status=416)
            response["Content-Range"] = f"bytes */{file_size}"
            return response

        length = end - start + 1
        response = StreamingHttpResponse(
            _read_range(fullpath.open("rb"), start, length),
            status=206,
            content_type=content_type,
        )
        response["Content-Length"] = str(length)
        response["Content-Range"] = f"bytes {start}-{end}/{file_size}"
        response["Content-Disposition"] = f'inline; filename="{fullpath.name}"'
    else:
        response = FileResponse(fullpath.open("rb"), content_type=content_type)
        response["Content-Length"] = str(file_size)

    response["Accept-Ranges"] = "bytes"
    response["Last-Modified"] = http_date(statobj.st_mtime)
    if encoding:
        response["Content-Encoding"] = encoding
    return response
