"""Tiny stdio MCP wrapper for signed-agent-receipts.

The wrapper intentionally exposes a small, auditable surface for agents:
create a local signed receipt from a JSON payload and verify a receipt JSONL.
It implements enough JSON-RPC 2.0/MCP framing for Hermes/Claude/Codex stdio
clients without adding a runtime dependency.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from .jsonl import read_jsonl, write_jsonl
from .records import make_record
from .signing import verify_record

SERVER_INFO = {"name": "signed-agent-receipts", "version": "0.1.0"}
TOOLS = [
    {
        "name": "create_signed_receipt",
        "description": "Create a signed agent receipt JSONL from a minimal record payload.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "out": {"type": "string", "description": "Output JSONL path."},
                "title": {"type": "string"},
                "source_runtime": {"type": "string"},
                "source_path": {"type": "string"},
                "actor": {"type": "string"},
                "summary": {"type": "string"},
                "signing_key": {"type": "string"},
            },
            "required": ["out"],
            "additionalProperties": True,
        },
    },
    {
        "name": "verify_receipt_jsonl",
        "description": "Verify all Ed25519 signatures in a signed-agent-receipts JSONL file.",
        "inputSchema": {
            "type": "object",
            "properties": {"jsonl": {"type": "string"}},
            "required": ["jsonl"],
            "additionalProperties": False,
        },
    },
]


def response(message_id: Any, result: Any = None, error: Any = None) -> dict[str, Any]:
    payload = {"jsonrpc": "2.0", "id": message_id}
    if error is not None:
        payload["error"] = error
    else:
        payload["result"] = result
    return payload


def text_result(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def handle_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == "create_signed_receipt":
        out = str(arguments.get("out") or "")
        if not out:
            raise ValueError("out is required")
        source_path = str(arguments.get("source_path") or out)
        record = make_record(
            source_runtime=str(arguments.get("source_runtime") or "agent"),
            source_path=source_path,
            title=str(arguments.get("title") or "Agent signed receipt"),
            actor=str(arguments["actor"]) if arguments.get("actor") is not None else None,
        )
        if arguments.get("summary"):
            record["inputs"].append({"type": "summary", "summary": str(arguments["summary"])})
        count = write_jsonl([record], out, key_path=arguments.get("signing_key"))
        result = verify_record(read_jsonl(out)[0])
        return text_result(f"wrote {count} signed receipt(s) to {out}; verify={result.status}")
    if name == "verify_receipt_jsonl":
        jsonl = arguments["jsonl"]
        records = read_jsonl(jsonl)
        results = [verify_record(record) for record in records]
        valid = sum(1 for item in results if item.valid)
        details = [f"{item.status}: {item.run_id or 'unknown'} ({item.reason})" for item in results]
        status = "ok" if valid == len(results) and records else "failed"
        return text_result(f"{status}: verified {valid}/{len(results)} records in {jsonl}\n" + "\n".join(details))
    raise ValueError(f"unknown tool: {name}")


def handle(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    message_id = message.get("id")
    if method == "initialize":
        return response(
            message_id,
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
            },
        )
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return response(message_id, {"tools": TOOLS})
    if method == "tools/call":
        params = message.get("params") or {}
        try:
            return response(message_id, handle_tool(str(params.get("name")), params.get("arguments") or {}))
        except Exception as exc:  # noqa: BLE001 - surfaced to MCP client
            return response(message_id, error={"code": -32000, "message": str(exc)})
    return response(message_id, error={"code": -32601, "message": f"method not found: {method}"})


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
            result = handle(message)
        except Exception as exc:  # noqa: BLE001 - stderr would break some stdio clients
            result = response(None, error={"code": -32700, "message": str(exc)})
        if result is not None:
            sys.stdout.write(json.dumps(result, separators=(",", ":")) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
