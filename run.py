"""
Entry point — run this file to start the AOR AR5700D WebSDR server.

Usage:
    python run.py

The server listens on all interfaces at the port configured in config.yaml
(default: 8080).  Open http://localhost:8080 in a browser to use the UI.

Windows note
------------
uvloop is Linux-only; uvicorn falls back to the standard asyncio event loop
on Windows automatically.  No extra configuration is needed.
"""
import sys
import uvicorn

# On Windows, the default ProactorEventLoop is fine for asyncio networking,
# but some third-party libraries work better with the SelectorEventLoop.
# Uncomment the block below if you encounter event loop issues on Windows:
#
# if sys.platform == "win32":
#     import asyncio
#     asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

if __name__ == "__main__":
    from backend.config import load_config
    cfg = load_config()

    print(f"Starting AOR AR5700D WebSDR on http://{cfg.server.host}:{cfg.server.port}")
    print("Press Ctrl+C to stop.\n")

    uvicorn.run(
        "backend.main:app",
        host=cfg.server.host,
        port=cfg.server.port,
        reload=False,
        workers=1,
        log_level="info",
    )
