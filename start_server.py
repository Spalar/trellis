"""Start the Trellis visualizer API server."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import uvicorn
from trellis import app

if __name__ == "__main__":
    print("Starting Trellis Visualizer API...")
    print("URL: http://127.0.0.1:17318")
    print("Press Ctrl+C to stop")
    print()

    # Local-only: this server has no authentication, so it must never bind
    # publicly. The UI it serves is same-origin, so no CORS is needed.
    uvicorn.run(app, host="127.0.0.1", port=17318)
