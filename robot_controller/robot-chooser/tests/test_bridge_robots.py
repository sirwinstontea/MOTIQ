"""The app adds / selects arms through the bridge's /robots/* proxy -> a real robot-chooser server."""
import threading
from http.server import ThreadingHTTPServer

import pytest

from conftest import REPO

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from robot_chooser.server import make_handler  # noqa: E402

import robot_control.arm_bridge as bridge_mod

KEY = "test-key-123"
H = {"X-Bridge-Key": KEY}
ARM = {"id": "kuka_kr3_r540", "brand": "KUKA", "model": "KR 3 R540", "dof": 6, "nominal_reach_m": 0.541,
       "nominal_payload_kg": 3, "library_url": "https://www.kuka.com/products/kr-3-r540",
       "library_file": "KR 3 R540.robot"}


@pytest.fixture
def client(ch):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(ch))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    proxy = bridge_mod.RobotChooserProxy(f"http://127.0.0.1:{httpd.server_address[1]}")
    # the robots routes never touch the Bridge (controller); None proves it
    yield TestClient(bridge_mod.create_app(None, KEY, chooser=proxy))
    httpd.shutdown()
    httpd.server_close()


def test_catalog_and_key(client):
    assert client.get("/robots/catalog").status_code == 401
    r = client.get("/robots/catalog", headers=H)
    assert r.status_code == 200
    assert any(a["id"] == "ur_ur5e" and a["active"] for a in r.json()["robots"])


def test_add_arm_then_select_is_blocked_until_configured(client, ch):
    r = client.post("/robots/add", json=ARM, headers=H)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["restart_required"] is True
    assert any(a["id"] == ARM["id"] and a["status"] != "Ready" for a in j["robots"])
    assert ch.load_catalog().get(ARM["id"]) is not None

    assert client.post("/robots/add", json=ARM, headers=H).status_code == 409   # duplicate

    r = client.post("/robots/select", json={"robot_id": ARM["id"]}, headers=H)
    assert r.status_code == 200
    assert r.json()["report"]["status"] == "BLOCKED"            # no verified limits / scene / workspace yet
    assert ch.load_hardware().active_robot == ARM["id"]


def test_proxy_rejects_bad_input(client):
    bad = {**ARM, "limits": {"verified": True}}                 # limits can't be set from the app
    assert client.post("/robots/add", json=bad, headers=H).status_code == 400
    assert client.post("/robots/add", json={**ARM, "id": "Bad Id"}, headers=H).status_code == 422
    assert client.post("/robots/add", json={**ARM, "library_url": "http://x"}, headers=H).status_code == 422
    assert client.post("/robots/select", json={"robot_id": "nope"}, headers=H).status_code == 404
    assert client.post("/robots/select", json={"robot_id": "ur_ur5e", "x": 1}, headers=H).status_code == 400
    assert client.post("/robots/remove", json={}, headers=H).status_code == 404   # not proxied


def test_not_configured_and_local_only():
    c = TestClient(bridge_mod.create_app(None, ""))
    assert c.get("/robots/catalog").status_code == 503
    with pytest.raises(ValueError):
        bridge_mod.RobotChooserProxy("http://192.168.1.5:8771")
