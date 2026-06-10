"""Batch 4 validation gate: headless MCP stdio server over the mocks.

Headless, no hardware, no API key, no network.  Starts
``python -m src.mcp_server.stdio_server`` as a SUBPROCESS in mock mode
(EMSTAT_MCP_PORT stripped from the environment) and speaks real MCP
over stdio using the installed ``mcp`` client:

* initialize the session and check the advertised server name,
* list_tools: >= 15 tools including run_cv and analyze_cv, every tool
  carrying an object inputSchema and a description,
* call_tool("device_status", {}) -> ok JSON (connected mock),
* call_tool("run_cv", channels [1, 2]) -> ok summary, num_points > 0
  (the full bridge/adapter/mock-engine path inside the server),
* call_tool("analyze_cv", demo .pssession) -> ok metrics,
* error path: unknown tool -> isError result, not an exception,

then closes the session and verifies the subprocess actually exits
(terminate as a fallback).  A hard watchdog force-exits with code 2
after 120 s so this gate can never hang.  Prints "SMOKE PASS" and
exits 0 on success.

Run from the repo root:
    python claude_test_files/smoke_mcp_server.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading

import psutil

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

WATCHDOG_SECONDS = 120.0
EXIT_WAIT_SECONDS = 15.0
MIN_TOOLS = 15
DEMO_SESSION = os.path.join(
    "claude_test_files", "data", "demo_cv_dpv_eis.pssession"
)
CV_INPUT = {
    "channels": [1, 2],
    "e_begin": -0.2,
    "e_vertex1": 0.3,
    "e_vertex2": -0.2,  # closed cycle: must equal e_begin on hardware
    "e_step": 0.05,
    "scan_rate": 0.5,
}


def _watchdog_fire() -> None:
    """Force-exit: the smoke gate must never hang."""
    print(
        "SMOKE FAIL: watchdog fired after %.0f s" % WATCHDOG_SECONDS,
        flush=True,
    )
    os._exit(2)


def _result_payload(result, failures, label):
    """Decode one CallToolResult into a JSON dict (or record a failure)."""
    if not result.content or result.content[0].type != "text":
        failures.append(f"{label}: no text content in result")
        return {}
    try:
        return json.loads(result.content[0].text)
    except ValueError as exc:
        failures.append(f"{label}: result is not JSON ({exc})")
        return {}


def _check_tools(tools, failures) -> None:
    """Validate the list_tools response."""
    names = [t.name for t in tools]
    if len(names) != len(set(names)):
        failures.append(f"duplicate tool names: {names!r}")
    if len(names) < MIN_TOOLS:
        failures.append(
            f"expected >= {MIN_TOOLS} tools, got {len(names)}: {names!r}"
        )
    for required in ("run_cv", "analyze_cv"):
        if required not in names:
            failures.append(f"tool {required!r} missing from list_tools")
    for tool in tools:
        schema = tool.inputSchema
        if not isinstance(schema, dict) or schema.get("type") != "object":
            failures.append(f"{tool.name}: inputSchema is not type 'object'")
        if not tool.description or len(tool.description) < 40:
            failures.append(f"{tool.name}: description missing or too short")
    by_name = {t.name: t for t in tools}
    if "run_cv" in by_name:
        props = by_name["run_cv"].inputSchema.get("properties", {})
        for prop in ("channels", "e_begin", "scan_rate"):
            if prop not in props:
                failures.append(f"run_cv schema missing property {prop!r}")


async def _scenario(failures: list[str]) -> None:
    """Drive the server subprocess over real MCP stdio."""
    env = {
        key: value
        for key, value in os.environ.items()
        if key != "EMSTAT_MCP_PORT"  # force mock mode
    }
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "src.mcp_server.stdio_server"],
        cwd=_REPO_ROOT,
        env=env,
    )

    me = psutil.Process()
    children_before = {p.pid for p in me.children(recursive=True)}

    async with stdio_client(params) as (read_stream, write_stream):
        # The server subprocess is our only new child.
        server_pids = [
            p.pid
            for p in me.children(recursive=True)
            if p.pid not in children_before
        ]
        if not server_pids:
            failures.append("could not identify the server subprocess pid")

        async with ClientSession(read_stream, write_stream) as session:
            init = await session.initialize()
            if init.serverInfo.name != "emstat-pico":
                failures.append(
                    f"server name wrong: {init.serverInfo.name!r}"
                )

            tools = (await session.list_tools()).tools
            _check_tools(tools, failures)

            # device_status: connected mock, engine idle.
            result = await session.call_tool("device_status", {})
            payload = _result_payload(result, failures, "device_status")
            if result.isError or payload.get("ok") is not True:
                failures.append(
                    f"device_status wrong: isError={result.isError}, "
                    f"{payload!r}"
                )
            elif not payload.get("connected"):
                failures.append(f"device_status not connected: {payload!r}")

            # run_cv: the full bridge/adapter/mock-engine path.
            result = await session.call_tool("run_cv", CV_INPUT)
            payload = _result_payload(result, failures, "run_cv")
            if result.isError or payload.get("ok") is not True:
                failures.append(
                    f"run_cv wrong: isError={result.isError}, {payload!r}"
                )
            else:
                if payload.get("technique") != "cv":
                    failures.append(f"run_cv technique != cv: {payload!r}")
                if not payload.get("num_points", 0) > 0:
                    failures.append(f"run_cv num_points not > 0: {payload!r}")
                if payload.get("measured_channels") != [1, 2]:
                    failures.append(
                        f"run_cv measured_channels != [1, 2]: "
                        f"{payload.get('measured_channels')!r}"
                    )

            # analyze_cv on the bundled demo session -> ok metrics.
            result = await session.call_tool(
                "analyze_cv", {"path": DEMO_SESSION}
            )
            payload = _result_payload(result, failures, "analyze_cv")
            if result.isError or payload.get("ok") is not True:
                failures.append(
                    f"analyze_cv wrong: isError={result.isError}, "
                    f"{payload!r}"
                )
            elif not isinstance(payload.get("metrics"), dict) or not payload[
                "metrics"
            ]:
                failures.append(f"analyze_cv metrics missing: {payload!r}")

            # Unknown tool -> isError result, never a raised exception.
            result = await session.call_tool("nope_no_such_tool", {})
            payload = _result_payload(result, failures, "unknown-tool")
            if not result.isError or "Unknown tool" not in payload.get(
                "error", ""
            ):
                failures.append(
                    f"unknown-tool path wrong: isError={result.isError}, "
                    f"{payload!r}"
                )

    # Transport closed: the server saw stdin EOF and must exit.  The
    # stdio_client already terminates as a fallback on context exit;
    # verify the pid is really gone, force-terminate if not.
    deadline = asyncio.get_running_loop().time() + EXIT_WAIT_SECONDS
    for pid in server_pids:
        while psutil.pid_exists(pid):
            if asyncio.get_running_loop().time() >= deadline:
                failures.append(
                    f"server subprocess {pid} still alive "
                    f"{EXIT_WAIT_SECONDS:.0f} s after session close; "
                    "terminating"
                )
                try:
                    psutil.Process(pid).terminate()
                except psutil.NoSuchProcess:
                    pass
                break
            await asyncio.sleep(0.1)


def main() -> int:
    """Entry point. Returns the process exit code."""
    if not os.path.isfile(os.path.join(_REPO_ROOT, DEMO_SESSION)):
        print(f"SMOKE FAIL: missing demo session {DEMO_SESSION!r}")
        return 1

    watchdog = threading.Timer(WATCHDOG_SECONDS, _watchdog_fire)
    watchdog.daemon = True
    watchdog.start()

    failures: list[str] = []
    try:
        asyncio.run(_scenario(failures))
    except BaseException as exc:  # noqa: BLE001 - report, don't hang
        failures.append(f"scenario raised: {exc!r}")
    watchdog.cancel()

    if failures:
        for failure in failures:
            print("SMOKE FAIL:", failure)
        return 1
    print("SMOKE PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
