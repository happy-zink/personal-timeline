#!/usr/bin/env python3
"""MCP server (stdio, JSON-RPC 2.0) for 私人数据时光轴.

An external AI agent / Claude / MiMo can connect with an API key and
read/write personal metrics through well-known tools.

Run:
  TIMELINE_API_KEY=tk_xxx TIMELINE_BASE=http://127.0.0.1:8764 python mcp/server.py

Or via MCP config (stdio):
  {
    "type": "local",
    "command": ["python", "mcp/server.py"],
    "environment": {
      "TIMELINE_API_KEY": "tk_...",
      "TIMELINE_BASE": "http://127.0.0.1:8764"
    },
    "enabled": true
  }

No third-party SDK — implements the MCP initialize / tools/list / tools/call
subset over stdin/stdout JSON-RPC.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

BASE = os.environ.get("TIMELINE_BASE", "http://127.0.0.1:8764").rstrip("/")
API_KEY = os.environ.get("TIMELINE_API_KEY", "").strip()

SERVER_INFO = {
    "name": "personal-timeline",
    "version": "2.0.0",
}

TOOLS = [
    {
        "name": "ai_ingest",
        "description": "用自然语言或结构化 JSON 写入一天的个人数据（体重/睡眠/心情/运动等）。缺失字段请省略或 null，不要写 0。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "自然语言，如「今天体重68kg，睡了7小时，心情不错」"},
                "date": {"type": "string", "description": "YYYY-MM-DD，可选"},
                "values": {"type": "object", "description": "结构化覆盖值"},
                "note": {"type": "string"},
                "confirm": {"type": "boolean", "description": "false=只解析不写入，默认 true"},
            },
            "required": [],
        },
    },
    {
        "name": "ai_parse",
        "description": "解析自然语言为结构化草稿，不写入数据库。",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "get_entry",
        "description": "读取某一天的记录。",
        "inputSchema": {
            "type": "object",
            "properties": {"date": {"type": "string", "description": "YYYY-MM-DD"}},
            "required": ["date"],
        },
    },
    {
        "name": "put_entry",
        "description": "写入/更新某一天。values 中 null 表示删除该指标。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "date": {"type": "string"},
                "values": {"type": "object"},
                "note": {"type": "string"},
            },
            "required": ["date"],
        },
    },
    {
        "name": "list_metrics",
        "description": "列出可用指标（key、名称、单位、范围）。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_trends",
        "description": "获取指标时间序列。缺失值为 null，绝不为 0。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "metrics": {"type": "string", "description": "逗号分隔，如 weight,sleep_hours"},
                "start": {"type": "string"},
                "end": {"type": "string"},
                "granularity": {"type": "string", "enum": ["day", "week", "month"]},
            },
            "required": ["metrics"],
        },
    },
    {
        "name": "search_notes",
        "description": "按关键词搜索备注。",
        "inputSchema": {
            "type": "object",
            "properties": {"q": {"type": "string"}, "start": {"type": "string"}, "end": {"type": "string"}},
        },
    },
]


def http(method: str, path: str, body: dict | None = None) -> Any:
    url = BASE + path
    data = None
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        payload = e.read().decode("utf-8", errors="replace")
        try:
            return json.loads(payload)
        except Exception:
            return {"ok": False, "error": payload, "status": e.code}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def call_tool(name: str, args: dict) -> dict:
    if not API_KEY:
        return {"ok": False, "error": "未设置 TIMELINE_API_KEY"}
    if name == "ai_ingest":
        body = {
            "text": args.get("text") or args.get("content"),
            "date": args.get("date"),
            "values": args.get("values"),
            "note": args.get("note"),
            "confirm": args.get("confirm", True),
        }
        body = {k: v for k, v in body.items() if v is not None}
        return http("POST", "/api/ai/ingest", body)
    if name == "ai_parse":
        return http("POST", "/api/ai/parse", {"text": args.get("text")})
    if name == "get_entry":
        return http("GET", f"/api/entries/{args.get('date')}")
    if name == "put_entry":
        return http("PUT", f"/api/entries/{args.get('date')}", {
            "values": args.get("values") or {},
            "note": args.get("note") or "",
        })
    if name == "list_metrics":
        return http("GET", "/api/metrics")
    if name == "get_trends":
        q = f"metrics={args.get('metrics')}"
        for k in ("start", "end", "granularity"):
            if args.get(k):
                q += f"&{k}={args[k]}"
        return http("GET", "/api/trends?" + q)
    if name == "search_notes":
        q = []
        for k in ("q", "start", "end"):
            if args.get(k):
                q.append(f"{k}={args[k]}")
        return http("GET", "/api/notes?" + "&".join(q))
    return {"ok": False, "error": f"unknown tool {name}"}


def send(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> None:
    if not API_KEY:
        print("TIMELINE_API_KEY 未设置，MCP 无法调用写入/读取接口", file=sys.stderr)
        print("请在服务内创建 API Key（/api/keys），再启动本进程", file=sys.stderr)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = req.get("method")
        rid = req.get("id")
        params = req.get("params") or {}

        if method == "initialize":
            send({
                "jsonrpc": "2.0",
                "id": rid,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": SERVER_INFO,
                },
            })
        elif method == "notifications/initialized":
            pass
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}})
        elif method == "tools/call":
            name = params.get("name")
            args = params.get("arguments") or {}
            try:
                result = call_tool(name, args)
                content_text = json.dumps(result, ensure_ascii=False, indent=2)
                send({
                    "jsonrpc": "2.0",
                    "id": rid,
                    "result": {
                        "content": [{"type": "text", "text": content_text}],
                        "isError": not result.get("ok", True),
                    },
                })
            except Exception as e:
                send({
                    "jsonrpc": "2.0",
                    "id": rid,
                    "result": {
                        "content": [{"type": "text", "text": f"错误: {e}"}],
                        "isError": True,
                    },
                })
        elif method == "ping":
            send({"jsonrpc": "2.0", "id": rid, "result": {}})
        else:
            if rid is not None:
                send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"Method not found: {method}"}})


if __name__ == "__main__":
    main()
