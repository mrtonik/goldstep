# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026 Michael Hupp
"""Minimal MCP (Model Context Protocol) client over stdio for UIBridgeServer.

The server speaks newline-delimited JSON-RPC 2.0 on stdin/stdout (the MCP stdio
transport): one JSON object per line, no Content-Length framing. We spawn it,
do the initialize handshake, then issue tools/call requests. Tool results come
back as {"content": [{"type": "text", "text": ...}], "isError": bool}; the text
payload is usually itself JSON, so call_json() parses it for callers.

Kept dependency-free (stdlib only) so it runs under a bare system python3 with no
venv, on Linux and FreeBSD alike.
"""

import json
import os
import select
import subprocess
import sys


class MCPError(Exception):
    """A JSON-RPC error response, or a tool call whose result was isError."""


class MCPClient:
    def __init__(self, server_argv, env=None, stderr_path=None, name="uibridge"):
        self._argv = server_argv
        self._env = env
        self._stderr_path = stderr_path
        self._name = name
        self._proc = None
        self._stderr = None
        self._id = 0

    # ---- lifecycle ----

    def start(self):
        # stderr goes to a file (not a pipe) so a chatty server can never wedge
        # us by filling an unread pipe buffer.
        self._stderr = open(self._stderr_path, "wb") if self._stderr_path else subprocess.DEVNULL
        self._proc = subprocess.Popen(
            self._argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._stderr,
            env=self._env,
            bufsize=1,
            text=True,
        )
        self._initialize()
        return self

    def close(self):
        if self._proc is None:
            return
        try:
            self._proc.stdin.close()
        except Exception:
            pass
        try:
            self._proc.terminate()
            self._proc.wait(timeout=5)
        except Exception:
            self._proc.kill()
        if self._stderr not in (None, subprocess.DEVNULL):
            self._stderr.close()
        self._proc = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.close()

    # ---- wire ----

    def _send(self, obj):
        line = json.dumps(obj, separators=(",", ":"))
        self._proc.stdin.write(line + "\n")
        self._proc.stdin.flush()

    def _read_line(self, timeout):
        # select() on the pipe fd so a hung/silent server fails fast instead of
        # blocking the whole suite. Returns the raw line or raises on timeout.
        fd = self._proc.stdout
        r, _, _ = select.select([fd], [], [], timeout)
        if not r:
            raise MCPError("%s: timed out waiting for response (%gs)" % (self._name, timeout))
        line = fd.readline()
        if line == "":
            code = self._proc.poll()
            raise MCPError("%s: server closed stdout (exit=%s)" % (self._name, code))
        return line

    def _request(self, method, params=None, timeout=30):
        self._id += 1
        rid = self._id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        # Drain until our id comes back; skip notifications and any non-JSON log
        # noise the server might print on stdout.
        while True:
            line = self._read_line(timeout)
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("id") != rid:
                continue
            if "error" in msg:
                raise MCPError("%s: %s -> %s" % (self._name, method, msg["error"]))
            return msg.get("result", {})

    def _notify(self, method, params=None):
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    # ---- MCP ----

    def _initialize(self):
        self._request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "goldstep", "version": "1.0"},
            },
        )
        self._notify("notifications/initialized")

    def list_tools(self, timeout=30):
        return self._request("tools/list", {}, timeout).get("tools", [])

    def call(self, name, arguments=None, timeout=30):
        """Call a tool, returning the raw result dict."""
        result = self._request(
            "tools/call", {"name": name, "arguments": arguments or {}}, timeout
        )
        if result.get("isError"):
            raise MCPError("%s: tool %s reported error: %s" % (self._name, name, _text(result)))
        return result

    def call_text(self, name, arguments=None, timeout=30):
        """Call a tool, returning the concatenated text content as a string."""
        return _text(self.call(name, arguments, timeout))

    def call_json(self, name, arguments=None, timeout=30):
        """Call a tool whose text content is JSON; return the parsed object.

        Falls back to the raw string if the payload isn't valid JSON.
        """
        txt = self.call_text(name, arguments, timeout)
        try:
            return json.loads(txt)
        except ValueError:
            return txt


def _text(result):
    parts = []
    for item in result.get("content", []) or []:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(item.get("text", ""))
    return "".join(parts)


if __name__ == "__main__":
    # Smoke check: spawn the server, list tools, print them. Usage:
    #   python3 -m goldstep.mcp [/path/to/UIBridgeServer]
    server = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("UIBRIDGE_SERVER")
    if not server:
        sys.exit("set UIBRIDGE_SERVER or pass the server path")
    with MCPClient([server]) as c:
        tools = c.list_tools()
        print("connected; %d tools" % len(tools))
        for t in tools:
            print("  -", t.get("name"))
