import os
import unittest
from unittest.mock import patch
from flask import Flask
from access_control import install_access_control


class AccessTest(unittest.TestCase):
    def make_client(self, protected=True):
        app = Flask(__name__, template_folder="../templates", static_folder="../static")
        with patch.dict(os.environ, {"APP_PASSWORD": "test-password" if protected else "", "APP_PUBLIC_ORIGIN": ""}):
            install_access_control(app)
        app.add_url_rule("/", "index", lambda: "ok")
        app.add_url_rule("/api/check", "check", lambda: {"ok": True}, methods=["GET", "POST"])
        return app.test_client()

    def test_disabled(self):
        c = self.make_client(False)
        self.assertEqual(c.post("/api/check").status_code, 200)

    def test_local_mode_pins_host_and_guards_youtube_writes(self):
        c = self.make_client(False)
        self.assertEqual(c.get("/", headers={"Host": "evil.test"}).status_code, 400)
        self.assertEqual(c.get("/", headers={"Host": "127.0.0.1:8765"}).status_code, 200)
        self.assertEqual(c.get("/", headers={"Host": "localhost:8765"}).status_code, 200)
        app = Flask(__name__)
        with patch.dict(os.environ, {"APP_PASSWORD": "", "APP_PUBLIC_ORIGIN": ""}):
            install_access_control(app)
        app.add_url_rule("/api/youtube/x", "yt", lambda: {"ok": True}, methods=["GET", "POST"])
        y = app.test_client()
        self.assertEqual(y.get("/api/youtube/x").status_code, 200)
        self.assertEqual(y.post("/api/youtube/x").status_code, 403)
        self.assertEqual(y.post("/api/youtube/x", headers={"Origin": "https://evil.test"}).status_code, 403)
        self.assertEqual(y.post("/api/youtube/x", headers={"Origin": "http://localhost"}).status_code, 200)
        with patch.dict(os.environ, {"APP_PASSWORD": "", "APP_PUBLIC_ORIGIN": "", "HOST": "0.0.0.0"}):
            lan = Flask(__name__); install_access_control(lan); lan.add_url_rule("/", "i", lambda: "ok")
        self.assertEqual(lan.test_client().get("/", headers={"Host": "192.168.0.5:8765"}).status_code, 200)

    def test_auth_and_csrf(self):
        c = self.make_client()
        self.assertEqual(c.get("/").status_code, 302)
        self.assertEqual(c.get("/api/check").status_code, 401)
        with c.get("/static/favicon.svg") as response:
            self.assertEqual(response.status_code, 200)
        self.assertEqual(c.get("/login").status_code, 200)
        self.assertEqual(c.post("/login", data={"password": "test-password"}).status_code, 403)
        h = {"Origin": "http://localhost"}
        self.assertEqual(c.post("/login", data={"password": "wrong"}, headers=h).status_code, 401)
        response = c.post("/login?next=//evil.test", data={"password": "test-password"}, headers=h)
        self.assertEqual(response.location, "/")
        self.assertEqual(c.get("/").status_code, 200)
        self.assertEqual(c.post("/api/check", headers={"Origin": "https://evil.test"}).status_code, 403)
        self.assertEqual(c.post("/api/check", headers=h).status_code, 200)
        self.assertEqual(c.post("/logout", headers=h).status_code, 302)
        self.assertEqual(c.get("/").status_code, 302)

    def test_limit(self):
        c = self.make_client()
        for _ in range(5):
            self.assertEqual(c.post("/login", data={"password": "wrong"}, headers={"Origin": "http://localhost"}).status_code, 401)
        self.assertEqual(c.post("/login", data={"password": "test-password"}, headers={"Origin": "http://localhost"}).status_code, 429)


if __name__ == "__main__":
    unittest.main()
