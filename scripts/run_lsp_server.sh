#!/bin/bash
# Run Pisa with ASGI support (WebSocket LSP bridge)

cd "$(dirname "$0")" || exit 1

# Activate venv
if [ -f .venv/bin/activate ]; then
    . .venv/bin/activate
fi

# Optional: Set Lean LSP command if not already in .env
# Uncomment and adjust as needed:
# export LEAN_LSP_CMD="/path/to/lean --server"

echo "Starting Pisa with Daphne ASGI server..."
echo "LSP WebSocket available at ws://localhost:8000/ws/lean-lsp/"
echo "Open http://localhost:8000 in browser"
echo ""

# Use daphne for ASGI (WebSocket support) instead of standard runserver
python3 -m daphne -b 0.0.0.0 -p 8000 pisa.asgi:application
