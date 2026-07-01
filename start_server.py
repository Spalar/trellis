"""Start the Trellis visualizer API server."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import uvicorn
from trellis import app

if __name__ == "__main__":
    print("Starting Trellis Visualizer API...")
    print("URL: http://localhost:17318")
    print("Press Ctrl+C to stop")
    print()

    uvicorn.run(app, host="0.0.0.0", port=17318)
