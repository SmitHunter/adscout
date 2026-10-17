import asyncio
import json

from .conftest import Env, seed


def _parse(text: str) -> list[tuple[str, dict[str, object]]]:
    events: list[tuple[str, dict[str, object]]] = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        lines = [ln for ln in block.split("\n") if ln and not ln.startswith(":")]
        kind = next((ln[7:] for ln in lines if ln.startswith("event: ")), None)
        data = next((ln[6:] for ln in lines if ln.startswith("data: ")), None)
        if kind is not None and data is not None and kind != "ping":
            events.append((kind, json.loads(data)))
    return events


async def test_sse_streams_progress_then_done(env: Env) -> None:
    r = await env.client.post("/api/searches", json={"keyword": "posture corrector"})
    sid = r.json()["snapshot_id"]
    await env.source.started.wait()

    task = asyncio.ensure_future(env.client.get(f"/api/searches/{sid}/events"))
    await asyncio.sleep(0.05)
    env.source.gate.set()
    resp = await asyncio.wait_for(task, 5)

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = _parse(resp.text)
    kinds = [k for k, _ in events]
    assert kinds[0] == "progress" and kinds[-1] == "done"
    assert set(kinds[:-1]) == {"progress"}
    first = events[0][1]
    assert first["snapshot_id"] == sid
    assert first["phase"] == "Scrolling for more ads"
    assert first["ads_found"] == 2 and first["advertisers"] == 1
    last = events[-1][1]
    assert last["kind"] == "done" and last["phase"] == "Done"


async def test_sse_finished_snapshot_yields_terminal(env: Env) -> None:
    sid = seed(env.engine, status="capped")
    resp = await env.client.get(f"/api/searches/{sid}/events")
    events = _parse(resp.text)
    assert [k for k, _ in events] == ["done"]
    assert events[0][1]["ads_found"] == 4


async def test_sse_unknown_snapshot_404(env: Env) -> None:
    resp = await env.client.get("/api/searches/999/events")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Snapshot 999 not found."}
