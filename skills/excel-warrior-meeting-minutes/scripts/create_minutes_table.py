#!/usr/bin/env python3
"""DEV-ONLY 一次性:在既有 Base 里建 Meeting Minutes 表(7 个文本列),打印 table_id。
跑完把输出的 table_id 填进 meeting_minutes_bitable.MINUTES_TABLE。不进 dispatch,不部署。

用法(需 FEISHU_APP_ID/SECRET/BITABLE_APP_TOKEN 或 ~/.hermes/.env):
    python3 skills/meeting-minutes/scripts/create_minutes_table.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]
                       / "project-tracking-portal" / "scripts"))
from feishu_bitable import _config  # noqa: E402

TABLE_NAME = "Meeting Minutes"
# 全部文本列(field_type=1);Minute ID 作幂等主键,放首列
FIELDS = ["Minute ID", "Maison", "Date", "Title", "Participants", "Content", "Action Items"]


def main() -> int:
    for k in ("http_proxy", "https_proxy", "all_proxy",
              "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        os.environ.pop(k, None)
    os.environ["no_proxy"] = os.environ["NO_PROXY"] = "*"

    cfg = _config()
    if cfg is None:
        print("feishu 未配置 (需 FEISHU_APP_ID/SECRET/BITABLE_APP_TOKEN)", file=sys.stderr)
        return 1

    import lark_oapi as lark
    from lark_oapi.api.bitable.v1 import (AppTableField,
                                          CreateAppTableRequest,
                                          CreateAppTableRequestBody, ReqTable)
    client = (lark.Client.builder().app_id(cfg["app_id"])
              .app_secret(cfg["app_secret"]).log_level(lark.LogLevel.WARNING).build())
    fields = [AppTableField.builder().field_name(n).type(1).build() for n in FIELDS]
    req = (CreateAppTableRequest.builder().app_token(cfg["app_token"])
           .request_body(CreateAppTableRequestBody.builder()
                         .table(ReqTable.builder()
                                .name(TABLE_NAME)
                                .default_view_name("Grid")
                                .fields(fields).build()).build()).build())
    resp = client.bitable.v1.app_table.create(req)
    if not resp.success():
        print(f"create table failed: code={resp.code} msg={resp.msg}", file=sys.stderr)
        return 1
    print(f"MINUTES_TABLE = \"{resp.data.table_id}\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
