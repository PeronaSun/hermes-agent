"""HTML 图片编辑工具 — 注册为 agent 工具,编辑某 sheet 图片区(画廊)。

只改 live HTML 的 .image-gallery 与 images/ 文件夹,绝不碰文字 <table>。
新图来自 URL(http(s):// 或测试用 file://),下载后存入 images/。
"""
import json, os, urllib.parse, urllib.request
from datetime import datetime

from html_editor_tool import _load_html, _save_html, _get_all_sheets, HTML_FILE_PATH
import build_html

IMAGES_DIR = "images"
MAX_BYTES = 20 * 1024 * 1024
_CT_EXT = {
    "image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg",
    "image/gif": "gif", "image/webp": "webp", "image/svg+xml": "svg",
}
_OK_EXT = {"png", "jpg", "jpeg", "gif", "webp", "svg"}


def _log_preview():
    """在 _save_html 之后调用;返回 preview dict 或 None。日志失败绝不影响主功能。"""
    try:
        import change_logger
        change_logger.init_db()
        return change_logger.preview_change_log(source="tool")
    except Exception as e:
        import sys
        print(f"[change_log warning] {e}", file=sys.stderr)
        return None


# ── 定位 helpers ───────────────────────────────────────────────────────────────

def _locate_sheet(soup, sheet_name):
    """返回 (tab序号, 第N个.sheet-content div)。找不到返回 (None, None)。"""
    names = _get_all_sheets(soup)
    for i, n in enumerate(names):
        if n.lower() == sheet_name.lower():
            divs = soup.select(".sheet-content")
            return i, (divs[i] if i < len(divs) else None)
    return None, None


def _get_gallery(div):
    return div.find("div", class_="image-gallery") if div else None


def _gallery_imgs(div):
    g = _get_gallery(div)
    return g.find_all("img", class_="gallery-img") if g else []


# ── 动作:list_images ─────────────────────────────────────────────────────────

def list_images(sheet_name):
    soup = _load_html(HTML_FILE_PATH)
    idx, div = _locate_sheet(soup, sheet_name)
    if div is None:
        return {"ok": False, "error": f"Sheet '{sheet_name}' not found. Available: {_get_all_sheets(soup)}"}
    imgs = _gallery_imgs(div)
    return {"ok": True, "count": len(imgs),
            "images": [{"index": i + 1, "src": im.get("src")} for i, im in enumerate(imgs)]}


# ── 下载/命名 helpers ─────────────────────────────────────────────────────────

def _download_image(url):
    """下载 url,返回 (bytes, ext)。非图片/超限/失败抛异常。"""
    req = urllib.request.Request(url, headers={"User-Agent": "html-image-tool/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        ct = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        data = resp.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError(f"image exceeds {MAX_BYTES} bytes")
    if not data:
        raise ValueError("downloaded 0 bytes")
    ext = _CT_EXT.get(ct)
    if ext is None:
        path_ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower().lstrip(".")
        if path_ext in _OK_EXT:
            ext = "jpg" if path_ext == "jpeg" else path_ext
    if ext is None:
        raise ValueError(f"URL is not a recognized image (content-type={ct!r})")
    return data, ext


def _save_upload(idx, sheet_name, data, ext):
    os.makedirs(IMAGES_DIR, exist_ok=True)
    slug = build_html.slugify(sheet_name)
    base = f"{idx:02d}_{slug}_upload_{datetime.now().strftime('%Y%m%d%H%M%S_%f')}"
    fname = f"{base}.{ext}"
    n = 1
    while os.path.exists(os.path.join(IMAGES_DIR, fname)):
        fname = f"{base}_{n}.{ext}"
        n += 1
    with open(os.path.join(IMAGES_DIR, fname), "wb") as f:
        f.write(data)
    return f"{IMAGES_DIR}/{fname}"


def _img_tag(soup, src):
    img = soup.new_tag("img")
    img["class"] = "gallery-img"
    img["src"] = src
    img["loading"] = "lazy"
    img["alt"] = ""
    return img


def _maybe_remove_file(soup, src):
    if not src or not src.startswith(IMAGES_DIR + "/"):
        return
    still_used = any(i.get("src") == src for i in soup.find_all("img"))
    if not still_used and os.path.exists(src):
        os.remove(src)


# ── 动作:add_image ────────────────────────────────────────────────────────────

def add_image(sheet_name, image_url, position=None):
    soup = _load_html(HTML_FILE_PATH)
    idx, div = _locate_sheet(soup, sheet_name)
    if div is None:
        return {"ok": False, "error": f"Sheet '{sheet_name}' not found. Available: {_get_all_sheets(soup)}"}
    try:
        data, ext = _download_image(image_url)
    except Exception as e:
        return {"ok": False, "error": f"download failed: {e}"}
    src = _save_upload(idx, sheet_name, data, ext)
    img = _img_tag(soup, src)
    gallery = _get_gallery(div)
    if gallery is None:
        gallery = soup.new_tag("div")
        gallery["class"] = "image-gallery"
        div.append(gallery)
    imgs = gallery.find_all("img", class_="gallery-img")
    if position is None or int(position) > len(imgs):
        gallery.append(img)
        pos = len(imgs) + 1
    else:
        p = max(1, int(position))
        imgs[p - 1].insert_before(img)
        pos = p
    _save_html(soup, HTML_FILE_PATH)
    out = {"ok": True, "sheet": sheet_name, "src": src, "position": pos, "count": len(imgs) + 1}
    preview = _log_preview()
    if preview:
        out["change_log"] = preview
    return out


# ── 动作:replace_image ────────────────────────────────────────────────────────

def replace_image(sheet_name, index, image_url):
    soup = _load_html(HTML_FILE_PATH)
    idx, div = _locate_sheet(soup, sheet_name)
    if div is None:
        return {"ok": False, "error": f"Sheet '{sheet_name}' not found. Available: {_get_all_sheets(soup)}"}
    imgs = _gallery_imgs(div)
    if index < 1 or index > len(imgs):
        return {"ok": False, "error": f"index {index} out of range (1-{len(imgs)})"}
    try:
        data, ext = _download_image(image_url)
    except Exception as e:
        return {"ok": False, "error": f"download failed: {e}"}
    new_src = _save_upload(idx, sheet_name, data, ext)
    target = imgs[index - 1]
    old_src = target.get("src")
    target["src"] = new_src
    _save_html(soup, HTML_FILE_PATH)
    _maybe_remove_file(soup, old_src)
    out = {"ok": True, "sheet": sheet_name, "index": index, "old_src": old_src, "new_src": new_src}
    preview = _log_preview()
    if preview:
        out["change_log"] = preview
    return out


# ── 动作:delete_image ────────────────────────────────────────────────────────

def delete_image(sheet_name, index):
    soup = _load_html(HTML_FILE_PATH)
    idx, div = _locate_sheet(soup, sheet_name)
    if div is None:
        return {"ok": False, "error": f"Sheet '{sheet_name}' not found. Available: {_get_all_sheets(soup)}"}
    gallery = _get_gallery(div)
    imgs = gallery.find_all("img", class_="gallery-img") if gallery else []
    if index < 1 or index > len(imgs):
        return {"ok": False, "error": f"index {index} out of range (1-{len(imgs)})"}
    old_src = imgs[index - 1].get("src")
    imgs[index - 1].decompose()
    remaining = gallery.find_all("img", class_="gallery-img")
    if not remaining:
        gallery.decompose()
    _save_html(soup, HTML_FILE_PATH)
    _maybe_remove_file(soup, old_src)
    out = {"ok": True, "sheet": sheet_name, "deleted_index": index, "remaining": len(remaining)}
    preview = _log_preview()
    if preview:
        out["change_log"] = preview
    return out


# ── 动作:reorder_images ──────────────────────────────────────────────────────

def reorder_images(sheet_name, new_order):
    soup = _load_html(HTML_FILE_PATH)
    idx, div = _locate_sheet(soup, sheet_name)
    if div is None:
        return {"ok": False, "error": f"Sheet '{sheet_name}' not found. Available: {_get_all_sheets(soup)}"}
    gallery = _get_gallery(div)
    imgs = gallery.find_all("img", class_="gallery-img") if gallery else []
    n = len(imgs)
    try:
        order = [int(x) for x in new_order]
    except (TypeError, ValueError):
        return {"ok": False, "error": f"new_order must be a list of ints, got {new_order}"}
    if sorted(order) != list(range(1, n + 1)):
        return {"ok": False, "error": f"new_order must be a permutation of 1..{n}, got {new_order}"}
    snapshot = list(imgs)
    for im in snapshot:
        im.extract()
    for pos in order:
        gallery.append(snapshot[pos - 1])
    _save_html(soup, HTML_FILE_PATH)
    out = {"ok": True, "sheet": sheet_name, "order": order}
    preview = _log_preview()
    if preview:
        out["change_log"] = preview
    return out


# ── Dispatcher ────────────────────────────────────────────────────────────────

def html_image_editor(action, **kwargs):
    """Agent tool 入口,返回 JSON 字符串。"""
    drift = None
    try:
        import change_logger
        change_logger.init_db()
        drift = change_logger.ensure_logged()
    except Exception:
        drift = None
    dispatch = {
        "list_images": lambda: list_images(kwargs["sheet_name"]),
        "add_image": lambda: add_image(kwargs["sheet_name"], kwargs["image_url"], kwargs.get("position")),
        "replace_image": lambda: replace_image(kwargs["sheet_name"], kwargs["index"], kwargs["image_url"]),
        "delete_image": lambda: delete_image(kwargs["sheet_name"], kwargs["index"]),
        "reorder_images": lambda: reorder_images(kwargs["sheet_name"], kwargs["new_order"]),
        "commit_change_log": lambda: {"ok": True, **__import__("change_logger").commit_change_log(
            kwargs["batch_id"], kwargs.get("why"))},
    }
    if action not in dispatch:
        result = {"ok": False, "error": f"Unknown action '{action}'. Available: {list(dispatch.keys())}"}
    else:
        try:
            result = dispatch[action]()
        except KeyError as e:
            result = {"ok": False, "error": f"Missing required parameter: {e}"}
        except Exception as e:
            result = {"ok": False, "error": str(e)}
    if drift and isinstance(result, dict):
        result["pending_drift"] = drift
    return json.dumps(result, ensure_ascii=False, indent=2)


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "html_image_editor",
        "description": (
            "Edit the image gallery of a sheet in the HTML report. Images are display-only "
            "blocks below the text table; this tool never touches text cells. Use list_images "
            "first to see current images and their 1-based indices. New images come from a URL "
            "(downloaded into images/)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list_images", "add_image", "replace_image", "delete_image", "reorder_images"],
                    "description": "The image operation to perform.",
                },
                "sheet_name": {"type": "string", "description": "Sheet tab name (case-insensitive)."},
                "image_url": {"type": "string", "description": "URL of the new image (add_image / replace_image)."},
                "index": {"type": "integer", "description": "1-based image position within the sheet's gallery."},
                "position": {"type": "integer", "description": "add_image only: 1-based insert position; omit to append."},
                "new_order": {
                    "type": "array", "items": {"type": "integer"},
                    "description": "reorder_images only: a permutation of 1..N giving the new sequence of current indices.",
                },
            },
            "required": ["action", "sheet_name"],
        },
    },
}


# ── Quick test ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    print(html_image_editor("list_images", sheet_name="Givenchy"))
