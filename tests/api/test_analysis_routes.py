import asyncio

from adscout.analysis.backends import UNAVAILABLE

from .conftest import Env, seed, wait_idle

NOT_LOGGED_IN = "Claude Code isn't logged in. Run claude once in a terminal to sign in."


async def _wait_done(env: Env, sid: int) -> dict[str, object]:
    for _ in range(300):
        r = await env.client.get(f"/api/snapshots/{sid}/analysis")
        assert r.status_code == 200
        job = r.json()
        if job is not None and job["state"] != "running":
            return job  # type: ignore[no-any-return]
        await asyncio.sleep(0.01)
    raise AssertionError("analysis did not finish")


async def test_post_analysis_starts_job_and_get_reports_done(env: Env) -> None:
    sid = seed(env.engine)
    assert (await env.client.get(f"/api/snapshots/{sid}/analysis")).json() is None
    r = await env.client.post(f"/api/snapshots/{sid}/analysis", json={"force": False})
    assert r.status_code == 202
    job = r.json()
    assert job["snapshot_id"] == sid and job["state"] == "running"
    assert job["model"] == "claude-sonnet-5-5" and job["backend"] == "cli"
    assert job["creatives"] == 3 and job["started_at"].endswith("Z")
    done = await _wait_done(env, sid)
    assert done["state"] == "done" and done["creatives"] == 3 and done["message"] is None
    report = (await env.client.get(f"/api/snapshots/{sid}/report")).json()
    assert report["analysis"] is not None


async def test_double_post_returns_same_running_job(env: Env) -> None:
    sid = seed(env.engine)
    env.analyzer.gate = asyncio.Event()
    first = await env.client.post(f"/api/snapshots/{sid}/analysis", json={"force": False})
    second = await env.client.post(f"/api/snapshots/{sid}/analysis", json={"force": True})
    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()
    env.analyzer.gate.set()
    await _wait_done(env, sid)
    assert env.analyzer.calls == 1


async def test_post_analysis_body_defaults_force_false(env: Env) -> None:
    sid = seed(env.engine)
    r = await env.client.post(f"/api/snapshots/{sid}/analysis", json={})
    assert r.status_code == 202
    await _wait_done(env, sid)


async def test_unknown_snapshot_404(env: Env) -> None:
    r = await env.client.post("/api/snapshots/999/analysis", json={"force": False})
    assert r.status_code == 404
    assert (await env.client.get("/api/snapshots/999/analysis")).status_code == 404


async def test_zero_relevant_422(env: Env) -> None:
    sid = seed(env.engine, keyword="garden gnome")
    r = await env.client.post(f"/api/snapshots/{sid}/analysis", json={"force": False})
    assert r.status_code == 422
    assert r.json() == {"detail": "No relevant ads to analyze."}


async def test_unavailable_503_copy(env: Env) -> None:
    sid = seed(env.engine)
    env.analyzer.available = False
    r = await env.client.post(f"/api/snapshots/{sid}/analysis", json={"force": False})
    assert r.status_code == 503
    assert r.json() == {"detail": UNAVAILABLE}


async def test_analysis_error_reported_on_job(env: Env) -> None:
    sid = seed(env.engine)
    env.analyzer.fail = NOT_LOGGED_IN
    r = await env.client.post(f"/api/snapshots/{sid}/analysis", json={"force": False})
    assert r.status_code == 202
    job = await _wait_done(env, sid)
    assert job["state"] == "error" and job["message"] == NOT_LOGGED_IN


async def test_post_analysis_cross_origin_403(env: Env) -> None:
    sid = seed(env.engine)
    r = await env.client.post(
        f"/api/snapshots/{sid}/analysis",
        json={"force": False},
        headers={"Origin": "https://evil.example"},
    )
    assert r.status_code == 403
    assert env.analyzer.calls == 0


async def test_auto_analyze_detached_after_search(env: Env) -> None:
    env.analyzer.auto = True
    env.analyzer.gate = asyncio.Event()
    r = await env.client.post("/api/searches", json={"keyword": "posture corrector"})
    sid = r.json()["snapshot_id"]
    env.source.gate.set()
    await wait_idle(env.manager)
    assert (await env.client.get("/api/searches/active")).json() is None
    job = (await env.client.get(f"/api/snapshots/{sid}/analysis")).json()
    assert job["state"] == "running" and job["creatives"] == 2

    # A new search can start while Claude is still working.
    env.source.gate.clear()
    r = await env.client.post("/api/searches", json={"keyword": "yoga mat"})
    assert r.status_code == 201
    env.source.gate.set()
    await wait_idle(env.manager)

    env.analyzer.gate.set()
    done = await _wait_done(env, sid)
    assert done["state"] == "done"
