import unittest
from unittest.mock import patch

from local_runtime import environment

environment()
from starlette.requests import Request
from starlette.responses import Response

from driver.web import COOKIE, access, app, browser_key, home


def visit(host="127.0.0.1", cookie=None):
    headers = [(b"host", host.encode())]
    if cookie:
        headers.append((b"cookie", f"{COOKIE}={cookie}".encode()))
    return Request({"type": "http", "method": "GET", "path": "/", "scheme": "http",
                    "headers": headers, "query_string": b"", "client": ("127.0.0.1", 19001)})


class DirectEntryTests(unittest.TestCase):
    def test_new_browser_opens_without_code(self):
        response = home(visit())
        self.assertEqual(response.status_code, 200)
        paths = {r.path for r in app.routes}
        self.assertNotIn("/api/admin/session", paths)
        self.assertNotIn('type="password"', response.path.read_text(encoding="utf-8"))
        token = response.headers["set-cookie"].split(";", 1)[0].split("=", 1)[1]
        self.assertEqual(len(token), 64)
        later = Response()
        digest = browser_key(visit(cookie=token), later)
        self.assertNotEqual(digest, token)
        self.assertNotIn("set-cookie", later.headers)
        self.assertIsNone(browser_key(visit(cookie="bad")))

    def test_demo_behind_loopback_proxy_has_visitor_access(self):
        with patch.dict("os.environ", {"DRIVER_PUBLIC_SESSION": "1"}):
            self.assertEqual(access(visit()), (False, None))
            self.assertIn("Secure", home(visit()).headers["set-cookie"])
        with patch.dict("os.environ", {"DRIVER_PUBLIC_SESSION": "0"}):
            self.assertEqual(access(visit()), (True, None))
            self.assertEqual(access(visit("external.example")), (False, None))
