import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from robot_chooser.server import make_handler


@pytest.fixture
def url(ch):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(ch))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def _call(url, path, body=None):
    req = urllib.request.Request(url + path, method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_ui_and_state(url):
    with urllib.request.urlopen(url + "/", timeout=10) as r:
        assert b"robot-chooser" in r.read()
    code, s = _call(url, "/api/state")
    assert code == 200 and len(s["catalog"]) == 5 and s["report"]["status"] == "READY"


def test_select_contract(url):
    code, s = _call(url, "/robots/select", {"robot_id": "ur_ur3e"})
    assert code == 200 and s["report"]["robot_id"] == "ur_ur3e" and s["report"]["status"] == "BLOCKED"
    code, s = _call(url, "/robots/select", {"robot_id": "../ur_ur5e"})
    assert code == 404 and s["error"]["code"] == "UNKNOWN_ROBOT"
    code, s = _call(url, "/robots/active")
    assert code == 200 and s["selection"]["robot_id"] == "ur_ur3e"


def test_add_and_remove_robot_over_http(url):
    body = {"id": "abb_irb1200", "brand": "ABB", "model": "IRB 1200", "dof": 6, "nominal_reach_m": 0.7,
            "nominal_payload_kg": 5.0, "library_url": "https://www.kuka.com/library"}
    code, s = _call(url, "/api/robots", body)
    assert code == 200 and any(r["id"] == "abb_irb1200" for r in s["catalog"])
    assert _call(url, "/api/robots", body)[0] == 409
    code, s = _call(url, "/api/robots/abb_irb1200/delete", {})
    assert code == 200 and not any(r["id"] == "abb_irb1200" for r in s["catalog"])


def test_bad_input_is_422(url):
    code, s = _call(url, "/api/components/main_tool", {"spec": {"mass_kg": -1}})
    assert code == 422 and s["error"]["code"] == "VALIDATION_ERROR"
    code, s = _call(url, "/api/hardware", {"speed_percent": 101})
    assert code == 422


def test_components_over_http(url):
    body = {"id": "wrist_cam", "type": "camera", "name": "Wrist cam", "attached_to": "main_tool",
            "spec": {"role": "vision", "mass_kg": 0.07}}
    code, s = _call(url, "/api/components", body)
    assert code == 200 and s["report"]["carried_mass_kg"] == 1.07
    code, s = _call(url, "/api/components/wrist_cam", {"driver": "realsense"})
    assert code == 200 and any("read-only" in c["connection"] for c in s["report"]["components"])
    assert _call(url, "/api/components/main_tool/delete", {})[0] == 409      # still carries wrist_cam
    assert _call(url, "/api/components/wrist_cam/delete", {})[0] == 200
    with urllib.request.urlopen(url + "/components.js", timeout=10) as r:
        assert b"renderComponents" in r.read()
