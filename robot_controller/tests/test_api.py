import json
import time

from fastapi.testclient import TestClient

from robot_control.api.app import create_app
from tests.conftest import demo_task


def test_api_flow(make_executor):
    ex = make_executor()
    client = TestClient(create_app(ex))
    assert client.get("/health").json()["process"] == "ok"
    assert client.get("/robot/state").json()["mode"] == "simulation"
    assert "red_cube" in client.get("/scene").json()["objects"]
    r = client.post("/tasks", content=json.dumps(demo_task("api1")))
    assert r.status_code == 202
    for _ in range(100):
        st = client.get("/tasks/api1").json()
        if st["status"] in ("SUCCEEDED", "FAILED", "REJECTED", "CANCELLED"):
            break
        time.sleep(0.02)
    assert st["status"] == "SUCCEEDED"
    assert client.get("/tasks/nope").status_code == 404
    bad = client.post("/tasks", content=json.dumps({"task_id": "x", "actions": [{"action_id": "a", "action": "moveL"}]}))
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "UNKNOWN_ACTION"
    assert "NOT an emergency stop" in client.post("/robot/stop").json()["note"]


def test_live_status_and_dashboard(make_executor):
    ex = make_executor()
    client = TestClient(create_app(ex))
    assert "Robot Live Status" in client.get("/").text
    ex.submit(demo_task("live1"), wait=True)
    snap = client.get("/status").json()
    assert snap["task_id"] == "live1" and snap["state"] == "COMPLETED"
    assert snap["progress"]["done"] == snap["progress"]["total"] == len(snap["steps"])
    assert snap["robot"]["tcp_m"] and len(snap["robot"]["joints_deg"]) == 6


def test_operator_reset_clears_latched_cancel(make_executor):
    ex = make_executor()
    client = TestClient(create_app(ex))
    ex.submit(demo_task("rst1"), wait=True)
    ex.state = ex.state.__class__.CANCELLED          # as after a cancelled task
    assert client.post("/operator/reset", json={"operator": ""}).status_code == 422
    r = client.post("/operator/reset", json={"operator": "tester"})
    assert r.status_code == 200 and r.json()["executor_state"] == "IDLE"


def test_auth_protects_everything_but_health(make_executor):
    import base64
    client = TestClient(create_app(make_executor(), auth=("operator", "a-long-test-secret")))
    assert client.get("/health").status_code == 200
    r = client.get("/status")
    assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Basic")
    assert client.get("/", auth=("operator", "wrong-secret")).status_code == 401
    assert client.get("/", auth=("someone", "a-long-test-secret")).status_code == 401
    assert client.get("/", headers={"authorization": "Basic !!notbase64"}).status_code == 401
    assert "Robot Live Status" in client.get("/", auth=("operator", "a-long-test-secret")).text
    assert client.get("/status", headers={"authorization": "Bearer a-long-test-secret"}).status_code == 200
    token = base64.b64encode(b"operator:a-long-test-secret").decode()
    assert client.post("/robot/stop", headers={"authorization": f"Basic {token}"}).status_code == 200


def test_serve_refuses_remote_bind_without_password(monkeypatch):
    from robot_control import cli
    monkeypatch.delenv("ROBOT_API_PASSWORD", raising=False)
    assert cli.main(["serve", "--backend", "mock", "--host", "0.0.0.0"]) == 2
    monkeypatch.setenv("ROBOT_API_PASSWORD", "short")
    assert cli.main(["serve", "--backend", "mock", "--host", "0.0.0.0"]) == 2
