#!/usr/bin/env python3
"""读飞书云文档纯文本:给"从会议纪要文档导入"用的最小读取器。

只做一件事:URL/doc_id -> 文档纯文本(docx-v1 raw_content,tenant 身份)。
不解析内容、不拆纪要——那是模型在 execute_code 里的活。凭证复用主表
feishu_bitable 的 env 兜底逻辑(FEISHU_APP_ID/SECRET,不需要 BITABLE_APP_TOKEN)。

支持的链接形态:
- https://<tenant>.feishu.cn/docx/<doc_id>       -> 直接读 raw_content
- https://<tenant>.feishu.cn/wiki/<node_token>   -> 先解析 wiki 节点,若指向 docx 再读
- 裸 doc_id                                       -> 当 docx 读
明确不支持:旧版 /docs/(编辑器已淘汰)、/minutes/(妙记,另一套 API,拿到链接直接报错提示)。
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path

_BASE = "https://open.feishu.cn/open-apis"

_URL_RE = re.compile(r"https?://[^/]+/(docx|wiki|docs|minutes)/([A-Za-z0-9]+)")


def parse_doc_url(text: str) -> dict:
    """从用户输入里拿出 (kind, id)。裸 token 当 docx。纯函数,不碰网络。"""
    text = (text or "").strip()
    m = _URL_RE.search(text)
    if m:
        return {"kind": m.group(1), "id": m.group(2)}
    if re.fullmatch(r"[A-Za-z0-9]{20,40}", text):
        return {"kind": "docx", "id": text}
    return {"kind": None, "id": None}


def _credentials() -> dict | None:
    """FEISHU_APP_ID/SECRET:env 优先,~/.hermes/.env 兜底(同 feishu_bitable._config,
    但不要求 BITABLE_APP_TOKEN——读文档用不上)。"""
    from feishu_bitable import _read_env_file
    file_env = None
    out = {}
    for key in ("FEISHU_APP_ID", "FEISHU_APP_SECRET"):
        val = os.environ.get(key)
        if not val:
            if file_env is None:
                file_env = _read_env_file()
            val = file_env.get(key)
        out[key] = val
    if not all(out.values()):
        return None
    return out


@contextmanager
def _no_proxy():
    """境内直连 open.feishu.cn:临时清代理再还原(同 resync_minutes 的做法)。"""
    keys = ("http_proxy", "https_proxy", "all_proxy",
            "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")
    saved = {k: os.environ.get(k) for k in keys + ("no_proxy", "NO_PROXY")}
    try:
        for k in keys:
            os.environ.pop(k, None)
        os.environ["no_proxy"] = "*"
        os.environ["NO_PROXY"] = "*"
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _get_json(url: str, *, token: str | None = None, payload: dict | None = None) -> dict:
    """一次 HTTP 往返,返回解析后的 JSON;HTTP 错误也解析 body 一并抛 RuntimeError。"""
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode())
        except Exception:
            body = {}
        raise RuntimeError(
            f"HTTP {e.code} code={body.get('code')} msg={body.get('msg', '')}") from e


def _tenant_token(creds: dict) -> str:
    r = _get_json(f"{_BASE}/auth/v3/tenant_access_token/internal",
                  payload={"app_id": creds["FEISHU_APP_ID"],
                           "app_secret": creds["FEISHU_APP_SECRET"]})
    if r.get("code") != 0:
        raise RuntimeError(f"tenant_access_token failed: {r.get('msg')}")
    return r["tenant_access_token"]


def _resolve_wiki(node_token: str, token: str) -> str:
    """wiki 节点 -> 实际文档 obj_token(仅接受 obj_type=docx)。"""
    r = _get_json(f"{_BASE}/wiki/v2/spaces/get_node?token={node_token}", token=token)
    if r.get("code") != 0:
        raise RuntimeError(f"wiki node resolve failed: {r.get('msg')}")
    node = (r.get("data") or {}).get("node") or {}
    obj_type = node.get("obj_type")
    if obj_type != "docx":
        raise RuntimeError(f"wiki 节点指向的不是 docx 文档(obj_type={obj_type}),读不了")
    return node["obj_token"]


def read_doc(url_or_id: str) -> dict:
    """主入口:URL/doc_id -> {"ok": True, "doc_id", "content"}。永不抛异常。"""
    parsed = parse_doc_url(url_or_id)
    kind, doc_id = parsed["kind"], parsed["id"]
    if not kind:
        return {"ok": False, "error": f"认不出的链接/ID:{url_or_id!r}(要 /docx/ 或 /wiki/ 链接)"}
    if kind == "minutes":
        return {"ok": False, "error": "这是妙记链接(/minutes/),不是文档;妙记导入暂未支持,"
                                      "请贴会后生成的 AI 纪要文档(/docx/)链接"}
    if kind == "docs":
        return {"ok": False, "error": "旧版 /docs/ 文档不支持;请转存为新版文档后贴 /docx/ 链接"}
    creds = _credentials()
    if creds is None:
        return {"ok": False, "error": "feishu 未配置(需 FEISHU_APP_ID/FEISHU_APP_SECRET)"}
    try:
        with _no_proxy():
            token = _tenant_token(creds)
            if kind == "wiki":
                doc_id = _resolve_wiki(doc_id, token)
            r = _get_json(f"{_BASE}/docx/v1/documents/{doc_id}/raw_content", token=token)
        if r.get("code") != 0:
            raise RuntimeError(f"raw_content failed: code={r.get('code')} msg={r.get('msg')}")
        content = (r.get("data") or {}).get("content") or ""
        if not content.strip():
            return {"ok": False, "error": "文档读到了但内容为空"}
        return {"ok": True, "doc_id": doc_id, "content": content}
    except RuntimeError as e:
        msg = str(e)
        if "403" in msg or "permission" in msg.lower() or "1770032" in msg:
            msg += "(机器人无权读取:让文档 owner 把权限设为组织内可读,或把文档分享给机器人)"
        elif "404" in msg or "not exist" in msg.lower() or "1770002" in msg:
            msg += "(文档不存在或 ID 不对)"
        return {"ok": False, "error": msg}
    except Exception as e:  # 网络等意外情况也不抛,统一返回结构
        return {"ok": False, "error": f"unexpected: {e}"}


def read_local_docx(path: str) -> dict:
    """本地 .docx 文件 -> 纯文本。给"用户直接把文件传给 agent"这条路用(网关已下载缓存到
    本地,agent 拿到的是磁盘路径,不是链接),不碰网络、不走 read_doc。永不抛异常。"""
    p = Path(path)
    if not p.exists():
        return {"ok": False, "error": f"文件不存在:{path}"}
    if p.suffix.lower() != ".docx":
        return {"ok": False, "error": f"只支持 .docx,收到后缀:{p.suffix or '(无)'}"}
    try:
        import docx
    except ImportError:
        return {"ok": False, "error": "python-docx 未安装(pip install python-docx)"}
    try:
        document = docx.Document(str(p))
        parts = [para.text for para in document.paragraphs if para.text.strip()]
        for table in document.tables:
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells if c.text.strip()]
                if cells:
                    parts.append(" | ".join(cells))
        content = "\n".join(parts)
        if not content.strip():
            return {"ok": False, "error": "文档读到了但内容为空"}
        return {"ok": True, "content": content}
    except Exception as e:
        return {"ok": False, "error": f"解析失败: {e}"}


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: feishu_doc_reader.py <doc_url_or_id>", file=sys.stderr)
        return 2
    # 独立跑时需要 feishu_bitable 在 sys.path(主表 scripts 目录)
    sys.path.insert(0, str(Path.home() / ".hermes" / "skills" / "productivity"
                           / "project-tracking-portal" / "scripts"))
    result = read_doc(argv[1])
    print(json.dumps(result, ensure_ascii=False)[:2000] if not result.get("ok")
          else result["content"])
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
