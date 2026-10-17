"""Demo mode: a data folder of synthetic searches that never touches Meta or Claude.

This module holds only the constants the API and CLI share; the generator (Pillow drawing,
database writes) lives in :mod:`adscout.demo.generator` and is imported only by
``adscout demo``.
"""

from pathlib import Path

# The copy for the banner, for every disabled control's tooltip and for the 403 detail.
DEMO_COPY = "Demo data. Nothing here is real. Run uv run adscout to search for real."

# Relative to the working directory, like the real app's ``data`` folder.
DEMO_DATA_DIR = Path("data-demo")
DEMO_PORT = 8788
# Written into a demo folder; live commands refuse to run on a folder that has it.
DEMO_MARKER = "demo.json"
