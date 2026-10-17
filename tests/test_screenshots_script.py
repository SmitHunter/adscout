"""``scripts/screenshots.py`` only ever talks to a local AdScout server."""

import argparse
import asyncio
import importlib.util
import random
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "screenshots.py"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("adscout_screenshots", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered first: dataclasses look their module up while the script loads.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "base",
    [
        "http://127.0.0.1:8787",
        "http://localhost:5173/",
        "http://[::1]:8787",
    ],
)
def test_loopback_base_accepted(base: str) -> None:
    assert _load().loopback_base(base) == base.rstrip("/")


@pytest.mark.parametrize(
    "base",
    [
        "http://example.com:8787",
        "https://adscout.example",
        "http://192.168.1.5:8787",
        "http://localhost.evil.com",
        "file:///etc/passwd",
        "127.0.0.1:8787",
    ],
)
def test_non_loopback_base_refused(base: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError, match="local AdScout server"):
        _load().loopback_base(base)


def test_media_dir_is_demo_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Own-data shots never land in the committed docs/media folder."""
    monkeypatch.chdir(tmp_path)
    module = _load()
    assert module.is_media_dir(Path("docs/media"))
    assert module.is_media_dir(Path("docs/media/sub"))
    assert not module.is_media_dir(Path("docs/screenshots"))
    assert not module.is_media_dir(Path("docs/media-old"))


def test_own_mode_refuses_media_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    module = _load()
    args = argparse.Namespace(
        base="http://127.0.0.1:1", out="docs/media", snapshot=None, hero=False
    )
    assert asyncio.run(module.run_own(args)) == 2
    assert not (tmp_path / "docs" / "media").exists()


def test_save_png_keeps_under_budget(tmp_path: Path) -> None:
    """A frame too big as full colour is saved as a palette PNG under 600 KB."""
    from PIL import Image, ImageChops

    module = _load()
    # A soft gradient with fine grain: big as RGB, small once reduced to 256 colours.
    gradient = Image.linear_gradient("L").resize((1440, 900)).convert("RGB")
    grain = Image.frombytes("RGB", (1440, 900), random.Random(1).randbytes(1440 * 900 * 3))
    frame = ImageChops.add(gradient, grain.point(lambda v: v % 6))
    path = tmp_path / "shot.png"
    module.save_png(frame, path)
    assert path.stat().st_size <= module.MAX_PNG_BYTES
    saved = Image.open(path)
    assert saved.size == (1440, 900)
    assert saved.mode == "P"
