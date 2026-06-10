"""Headless MCP stdio server exposing the embedded-agent tool surface.

Thin Model Context Protocol (MCP) server for Claude Code and other MCP
clients.  It exposes EXACTLY the tool definitions of the in-app agent
-- the eleven measurement/device/export tools from :func:`src.agent.tools.
build_registry` plus the seven vendored-analysis tools from
:func:`src.agent.vendor_analysis.build_analysis_tools` -- and routes
every tool call through :func:`src.agent.tools.dispatch_tool`, so
behavior (parameter validation, structured errors, compact JSON
summaries) is identical to the agent embedded in the GUI app.

Engine modes (selected by the EMSTAT_MCP_PORT environment variable):

* EMSTAT_MCP_PORT unset/empty (default): no hardware required.  A
  ``MockMeasurementEngine`` + ``MockConnection`` pair serves synthetic
  data, so the server runs anywhere (CI, laptops without the device).
* EMSTAT_MCP_PORT set (e.g. ``EMSTAT_MCP_PORT=COM6``): a REAL
  ``PicoConnection`` on that serial port plus the real
  ``MeasurementEngine``.  The port is opened eagerly at startup; if
  that fails the server still starts (a warning is logged) and the
  model can retry via the ``connect_device`` tool.

Sample Claude Code ``.mcp.json`` entry (run from the repo root so the
``src`` package imports; add ``"env": {"EMSTAT_MCP_PORT": "COM6"}`` for
real hardware)::

    {
      "mcpServers": {
        "emstat-pico": {
          "command": "python",
          "args": ["-m", "src.mcp_server.stdio_server"],
          "cwd": "<repo>"
        }
      }
    }

Threading model (mirrors the GUI app's AgentWorker pattern, with the
Qt event loop on the main thread standing in for the GUI):

* MAIN thread: ``QCoreApplication`` + ``bridge.install()`` + the engine
  and connection objects, running ``app.exec()``.  The engine must be
  started from (and the connection owned by) this thread, exactly as in
  the GUI app.
* WORKER thread: ``asyncio.run(...)`` hosting the MCP stdio server.
  ``call_tool`` awaits :func:`dispatch_tool`, whose handlers marshal
  engine/connection work onto the main thread through the existing
  :mod:`src.agent.bridge` (``run_on_gui`` / ``await_signal``).
* When the MCP client disconnects (stdin EOF) the server coroutine
  returns, the worker thread queues ``app.quit()``, and the process
  exits cleanly after best-effort engine abort / port disconnect.

Figures: analysis tools are built with ``figure_sink=None``, so no
matplotlib figures are rendered and tool results carry the metric
summaries only (an MCP text transport has no figure panel; clients that
want plots can ask for the metrics and plot locally).

stdout is reserved for the MCP transport: ALL logging goes to stderr.
The module imports (and ``--help`` / ``--selftest`` run) with no API
key and no hardware.

Run from the repo root:
    python -m src.mcp_server.stdio_server [--selftest]
"""

from __future__ import annotations

# Eager native imports at module top, before any asyncio loop exists
# (blueprint constraint; avoids the Windows DLL-load deadlock).  The
# vendor_analysis import below additionally pulls numpy/scipy/
# matplotlib (Agg) / pandas eagerly.
import numpy  # noqa: F401  - eager native import

import argparse
import asyncio
import logging
import os
import sys
import threading
from typing import Any, Optional

from PyQt6.QtCore import QCoreApplication, QMetaObject, Qt

import mcp.types as mcp_types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from src.agent import bridge
from src.agent.engine_adapter import EngineAdapter
from src.agent.mock_engine import MockConnection, MockMeasurementEngine
from src.agent.tools import ToolRegistry, build_registry, dispatch_tool
from src.agent.vendor_analysis import build_analysis_tools
from src.comms.serial_connection import PicoConnection
from src.engine.measurement_engine import MeasurementEngine

logger = logging.getLogger(__name__)

__all__ = [
    "ENGINE_PORT_ENV",
    "SERVER_NAME",
    "build_mcp_server",
    "build_tool_registry",
    "main",
]

#: Environment variable selecting the real-hardware serial port.
ENGINE_PORT_ENV = "EMSTAT_MCP_PORT"

#: MCP server name advertised during initialization.
SERVER_NAME = "emstat-pico"


# ---------------------------------------------------------------------------
# Engine + registry construction
# ---------------------------------------------------------------------------

def _build_engine_pair() -> tuple[Any, Any, str]:
    """Build the engine + connection pair for the selected mode.

    Returns:
        ``(engine, connection, mode)`` where *mode* is a human-readable
        description for the startup log.  Mock mode (default) is fully
        connected on return; real mode attempts to open the port and
        logs a warning on failure (the model can retry with
        ``connect_device``).
    """
    port = os.environ.get(ENGINE_PORT_ENV, "").strip()
    if port:
        connection = PicoConnection(port)
        engine = MeasurementEngine()
        try:
            connection.connect(port)
        except Exception as exc:  # noqa: BLE001 - startup must not die
            logger.warning(
                "Could not open %s at startup (%s: %s); the "
                "connect_device tool can retry.",
                port, type(exc).__name__, exc,
            )
        return engine, connection, f"real hardware on {port!r}"
    connection = MockConnection()
    connection.connect("MOCK1")
    engine = MockMeasurementEngine()
    return engine, connection, "mock engine (no hardware)"


def build_tool_registry(adapter: EngineAdapter) -> ToolRegistry:
    """Build the full agent tool registry bound to *adapter*.

    Identical surface to the GUI app: the built-in measurement/device
    tools plus the vendored-analysis tools.  ``figure_sink=None`` --
    summaries only, no figures (see module docstring).

    Args:
        adapter: The :class:`EngineAdapter` (real or mock behind it).

    Returns:
        The populated :class:`ToolRegistry`.
    """
    return build_registry(
        adapter, extra_tools=build_analysis_tools(figure_sink=None)
    )


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

def build_mcp_server(registry: ToolRegistry) -> Server:
    """Build the low-level MCP server over *registry*.

    ``list_tools`` converts the registry's Anthropic tool definitions
    one-to-one into MCP ``Tool`` objects (``input_schema`` ->
    ``inputSchema``); ``call_tool`` routes through
    :func:`dispatch_tool`.  SDK-side input validation is DISABLED
    (``validate_input=False``) so parameter checking happens in exactly
    one place -- the adapter/dispatch layer -- and error payloads match
    the in-app agent byte for byte.

    Args:
        registry: The tool registry to expose.

    Returns:
        A configured ``mcp.server.lowlevel.Server`` (transport-less;
        the caller runs it over stdio).
    """
    server: Server = Server(SERVER_NAME)

    @server.list_tools()
    async def _list_tools() -> list[mcp_types.Tool]:
        return [
            mcp_types.Tool(
                name=tool_def["name"],
                description=tool_def["description"],
                inputSchema=tool_def["input_schema"],
            )
            for tool_def in registry.tool_defs
        ]

    @server.call_tool(validate_input=False)
    async def _call_tool(
        name: str, arguments: Optional[dict[str, Any]]
    ) -> mcp_types.CallToolResult:
        result_json, is_error = await dispatch_tool(
            registry, name, arguments
        )
        return mcp_types.CallToolResult(
            content=[mcp_types.TextContent(type="text", text=result_json)],
            isError=is_error,
        )

    return server


async def _serve_stdio(server: Server) -> None:
    """Run *server* over the process stdio until the client disconnects."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _selftest() -> int:
    """Headless self-check: build everything, start nothing.

    Constructs the Qt application, bridge, mock engine pair, registry,
    and MCP server, then prints the tool names and exits.  No MCP
    transport is opened, no hardware or API key is touched.

    Returns:
        Process exit code (0 on success).
    """
    app = QCoreApplication.instance() or QCoreApplication(sys.argv[:1])
    bridge.install()
    engine = MockMeasurementEngine()
    connection = MockConnection()
    connection.connect("MOCK1")
    registry = build_tool_registry(EngineAdapter(engine, connection))
    build_mcp_server(registry)
    del app
    print(f"SELFTEST PASS: {len(registry)} tools: "
          f"{', '.join(registry.names())}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    """Entry point: parse args, wire the engine, serve MCP over stdio.

    Args:
        argv: Optional argument list (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code: 0 on clean shutdown (client disconnect),
        1 when the MCP server failed.
    """
    parser = argparse.ArgumentParser(
        prog="python -m src.mcp_server.stdio_server",
        description=(
            "Headless MCP stdio server exposing the EmStat Pico agent "
            "tools. Default: mock engine, no hardware. Set "
            f"{ENGINE_PORT_ENV}=<port> (e.g. COM6) for real hardware."
        ),
    )
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="build the registry and MCP server, print the tool "
             "names, and exit without opening the stdio transport",
    )
    args = parser.parse_args(argv)

    # stdout belongs to the MCP transport: all logging goes to stderr.
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    if args.selftest:
        return _selftest()

    # MAIN thread: Qt application, bridge, engine + connection.
    app = QCoreApplication(sys.argv[:1])
    bridge.install()
    engine, connection, mode = _build_engine_pair()
    adapter = EngineAdapter(engine, connection)
    registry = build_tool_registry(adapter)
    server = build_mcp_server(registry)
    logger.info(
        "MCP stdio server %r ready (pid %d): %s, %d tools.",
        SERVER_NAME, os.getpid(), mode, len(registry),
    )

    exit_code = {"value": 0}

    def worker() -> None:
        """WORKER thread: own asyncio loop hosting the MCP server."""
        try:
            asyncio.run(_serve_stdio(server))
        except BaseException:  # noqa: BLE001 - report, then quit Qt
            logger.exception("MCP stdio server failed.")
            exit_code["value"] = 1
        finally:
            # Client disconnected (stdin EOF) or server failed: quit
            # the Qt loop from the main thread via a queued call.
            QMetaObject.invokeMethod(
                app, "quit", Qt.ConnectionType.QueuedConnection
            )

    thread = threading.Thread(target=worker, name="mcp-loop", daemon=True)
    thread.start()
    app.exec()
    thread.join(timeout=5.0)

    # Best-effort cleanup: never block process exit.
    try:
        if engine.isRunning():
            engine.abort()
        if connection.is_connected:
            connection.disconnect()
    except Exception:  # noqa: BLE001 - shutdown must not raise
        logger.exception("Cleanup after MCP shutdown failed.")
    logger.info("MCP stdio server stopped (exit %d).", exit_code["value"])
    return exit_code["value"]


if __name__ == "__main__":
    sys.exit(main())
