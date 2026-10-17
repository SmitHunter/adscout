"""Write web/openapi.json from the FastAPI app without starting a server.

Usage: uv run python scripts/dump_openapi.py [output path]
"""

import json
import sys
import tempfile
from pathlib import Path

from adscout.api.app import create_app
from adscout.config import Settings

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "web" / "openapi.json"
    with tempfile.TemporaryDirectory() as tmp:
        # A throwaway data dir: the schema never depends on the user's database.
        app = create_app(Settings(_env_file=None, data_dir=Path(tmp)))  # type: ignore[call-arg]
        schema = app.openapi()
        app.state.api.engine.dispose()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
