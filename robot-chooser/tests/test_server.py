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


def test_bad_input_is_422(url):
    code, s = _call(url, "/api/hardware", {"tool": {"mass_kg": -1}})
    assert code == 422 and s["error"]["code"] == "VALIDATION_ERROR"
