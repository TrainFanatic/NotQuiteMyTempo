"""The team hub: a WebSocket broadcast server. Every frame a client sends goes
to every OTHER client (no echo, no handshake) and is logged to
recordings/<time>.jsonl for replay. Promoted from fletcher/dev/mini_hub.py,
which stays as the same implementation for Fletcher's own tests.

    python -m hub.server [--port 8765] [--no-record]
"""

from fletcher.dev.mini_hub import main, run_hub  # noqa: F401  (one implementation)

if __name__ == "__main__":
    main()
