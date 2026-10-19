"""Exercise real application guards before any automation/device handler."""
import io
import re
import unittest
from unittest.mock import patch

from flask import jsonify
from flask.testing import FlaskClient
from werkzeug.test import EnvironBuilder
import mirror_server as m
from local_access import COOKIE, HEADER, LocalAccessPolicy

ORIGIN = "http://localhost:5000"
LAN_ORIGIN = "http://192.168.11.217:5000"

class LocalAccessTests(unittest.TestCase):
    def setUp(self):
        self.client = FlaskClient(m.app)
        self.headers = {HEADER: m.local_access.token, "Origin": ORIGIN}

    def call(self, path="/api/input", method="POST", **kwargs):
        kwargs.setdefault("base_url", ORIGIN)
        kwargs.setdefault("headers", self.headers)
        return self.client.open(path, method=method, **kwargs)

    def test_bootstrap_is_ephemeral_and_uncached(self):
        r = self.call("/", "GET", headers={})
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'name="qa-csrf-token"', r.data)
        self.assertIn(b'/static/local-request.js', r.data)
        self.assertEqual(r.headers["Cache-Control"], "no-store")
        self.assertEqual(r.headers["X-Frame-Options"], "DENY")
        cookie = r.headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertNotIn("Max-Age", cookie)
        self.assertNotIn("Expires", cookie)
        other = LocalAccessPolicy("127.0.0.1",5000,("localhost","127.0.0.1"))
        self.assertFalse(other.valid_token(m.local_access.token))

    def test_default_listener_config_and_validation(self):
        import local_access_config as c
        self.assertEqual((c.BIND_ADDRESS,c.PORT),("0.0.0.0",5000))
        self.assertEqual(c.ALLOWED_NETWORKS, ("192.168.11.0/24",))
        self.assertEqual(m.local_access.origins,{ORIGIN,"http://127.0.0.1:5000",LAN_ORIGIN})
        for args in [("0.0.0.0",5000,("localhost",)),("127.0.0.1",0,("localhost",)),
                     ("127.0.0.1",True,("localhost",)),("127.0.0.1",5000,("attacker.test",))]:
            with self.subTest(args=args), self.assertRaises(ValueError): LocalAccessPolicy(*args)

    def test_invalid_lan_config_is_rejected(self):
        for networks, hosts in [(("0.0.0.0/0",), ("192.168.11.217",)),
                                (("8.8.8.0/24",), ("8.8.8.8",)),
                                (("::/0",), ("localhost",)),
                                (("192.168.11.1/24",), ("192.168.11.217",)),
                                (("192.168.11.0/24",), ("192.168.12.1",)),
                                (("192.168.11.0/24",), ("attacker.test",))]:
            with self.subTest(networks=networks, hosts=hosts), self.assertRaises(ValueError):
                LocalAccessPolicy("0.0.0.0",5000,hosts,networks)

    def test_only_explicit_network_clients_are_allowed(self):
        for address in ("192.168.11.1", "192.168.11.20", "192.168.11.254", "127.0.0.1", "::1"):
            self.assertTrue(m.local_access.client_allowed(address), address)
        for address in ("192.168.10.254", "192.168.12.1", "10.0.0.2", "8.8.8.8",
                        "192.168.11.0", "192.168.11.255", "::ffff:192.168.11.20", None, "invalid"):
            self.assertFalse(m.local_access.client_allowed(address), address)
        loopback = LocalAccessPolicy("127.0.0.1",5000,("localhost",))
        self.assertFalse(loopback.client_allowed("192.168.11.20"))

    def lan_call(self, path, method="GET", **kwargs):
        kwargs.setdefault("headers", {HEADER:m.local_access.token, "Origin":LAN_ORIGIN})
        kwargs.setdefault("environ_base", {"REMOTE_ADDR":"192.168.11.20"})
        return self.call(path, method, base_url=LAN_ORIGIN, **kwargs)

    def test_lan_page_and_existing_read_routes(self):
        for path in ("/", "/api/runtime/status", "/api/execution/results", "/api/execution/sources", "/api/testcase"):
            with self.subTest(path=path):
                self.assertEqual(self.lan_call(path).status_code, 200)

    def test_lan_writes_still_require_page_and_same_origin(self):
        with patch.dict(m.app.view_functions,{"manual_input":lambda:jsonify(ok=True)}):
            self.assertEqual(self.lan_call("/api/input", "POST", json={}).get_json(), {"ok":True})
            for headers in ({}, {HEADER:m.local_access.token}, {HEADER:m.local_access.token,"Origin":ORIGIN},
                            {"Origin":LAN_ORIGIN}, {HEADER:"forged","Origin":LAN_ORIGIN}):
                with self.subTest(headers=headers):
                    self.assertEqual(self.lan_call("/api/input", "POST", headers=headers, json={}).status_code, 403)

    def test_lan_multipart_and_resource_cookie(self):
        self.lan_call("/", headers={})
        with patch.dict(m.app.view_functions,{"upload_testcase":lambda:jsonify(ok=True)}):
            self.assertEqual(self.lan_call("/api/testcase", "POST",
                data={"file":(io.BytesIO(b"fixture"),"fixture.xlsx")}).get_json(), {"ok":True})
        with patch.dict(m.app.view_functions,{"recording_export":lambda recording_id:jsonify(ok=True)}):
            # A screenshot uses the same named-resource cookie policy as workbook/Recorder export.
            with patch.dict(m.app.view_functions,{"result_screenshot":lambda filename:jsonify(ok=True)}):
                self.assertEqual(self.lan_call("/api/results/screenshot/fixture.png", headers={}).status_code, 200)

    def test_lan_address_spoofing_does_not_bypass_guard(self):
        for path in ("/", "/api/runtime/status", "/api/input"):
            with self.subTest(path=path):
                self.assertEqual(self.lan_call(path, environ_base={"REMOTE_ADDR":"192.168.12.20"},
                    headers={**self.headers,"X-Forwarded-For":"192.168.11.20"}).status_code, 403)
        self.assertEqual(self.lan_call("/", headers={"Host":"192.168.11.218:5000"}).status_code, 403)

    def test_lan_socket_transport_and_event_policy(self):
        from werkzeug.test import Client
        from werkzeug.wrappers import Response
        guard=m.local_access.wrap_socket_transport(Response("transport reached"))
        client=Client(guard,Response)
        for address, expected in (("192.168.11.20",200),("192.168.12.20",403)):
            r=client.get("/socket.io/",base_url=LAN_ORIGIN,headers={"Origin":LAN_ORIGIN},
                         environ_base={"REMOTE_ADDR":address})
            self.assertEqual(r.status_code, expected)
            with m.app.test_request_context("/socket.io/",base_url=LAN_ORIGIN,
                    headers={"Origin":LAN_ORIGIN},environ_base={"REMOTE_ADDR":address}):
                self.assertEqual(m.local_access.socket_allowed({"csrf_token":m.local_access.token}), expected==200)
                self.assertFalse(m.local_access.socket_allowed({"csrf_token":"forged"}))

    def test_bare_direct_api_and_missing_token_rejected(self):
        for path,method in [("/api/runtime/status","GET"),("/api/input","POST"),("/api/testcase","POST")]:
            with self.subTest(path=path,method=method):
                self.assertEqual(self.call(path,method,headers={"Origin":ORIGIN},json={}).status_code,403)

    def test_forged_token_rejected_before_handler(self):
        with patch.dict(m.app.view_functions,{"manual_input":lambda: self.fail("handler entered")}):
            self.assertEqual(self.call(headers={HEADER:"forged","Origin":ORIGIN},json={}).status_code,403)

    def test_non_ascii_token_fails_closed(self):
        self.assertFalse(m.local_access.valid_token("가짜"))

    def test_browser_cookie_alone_cannot_mutate(self):
        self.call("/","GET",headers={})
        self.assertEqual(self.call(headers={"Origin":ORIGIN},json={}).status_code,403)

    def test_bad_hosts_rejected_for_page_and_api(self):
        for host in ["evil.example:5000","localhost","localhost:5001","127.0.0.1.evil:5000","localhost.:5000"]:
            for path in ["/","/api/runtime/status"]:
                with self.subTest(host=host,path=path):
                    self.assertEqual(self.call(path,"GET",headers={**self.headers,"Host":host}).status_code,403)

    def test_each_explicit_host_allowed(self):
        for origin in sorted(m.local_access.origins):
            with self.subTest(origin=origin):
                self.assertEqual(self.call("/api/runtime/status","GET",base_url=origin,
                    headers={HEADER:m.local_access.token,"Origin":origin}).status_code,200)

    def test_remote_and_proxy_headers_rejected(self):
        self.assertEqual(self.call("/","GET",environ_base={"REMOTE_ADDR":"192.168.1.4"}).status_code,403)
        for header in ["Forwarded","X-Forwarded-Host","X-Forwarded-For","X-Forwarded-Proto","X-Forwarded-Port"]:
            with self.subTest(header=header):
                self.assertEqual(self.call("/","GET",headers={**self.headers,header:"localhost:5000"}).status_code,403)

    def test_null_cross_origin_and_absent_write_origin_rejected(self):
        for origin in [None,"null","https://localhost:5000","http://evil.test","http://127.0.0.1:5000",ORIGIN+"/"]:
            headers={HEADER:m.local_access.token}
            if origin is not None: headers["Origin"]=origin
            with self.subTest(origin=origin):
                self.assertEqual(self.call(headers=headers,json={}).status_code,403)

    def test_cross_site_metadata_rejected(self):
        self.assertEqual(self.call("/","GET",headers={**self.headers,"Sec-Fetch-Site":"cross-site"}).status_code,403)

    def test_plain_form_and_text_json_rejected(self):
        for content_type in ["text/plain","application/x-www-form-urlencoded"]:
            with self.subTest(content_type=content_type):
                self.assertEqual(self.call(data="{}",content_type=content_type).status_code,415)

    def test_malformed_or_non_object_json_rejected(self):
        for body in ["{", "[]", "null", '"text"', "1"]:
            with self.subTest(body=body):
                self.assertEqual(self.call(data=body,content_type="application/json").status_code,400)

    def test_empty_body_requires_explicit_json_object(self):
        self.assertEqual(self.call().status_code,415)
        self.assertEqual(self.call(data="",content_type="application/json").status_code,400)

    def test_valid_json_reaches_handler(self):
        with patch.dict(m.app.view_functions,{"manual_input":lambda:jsonify(ok=True)}):
            self.assertEqual(self.call(json={}).get_json(),{"ok":True})

    def test_delete_also_guarded(self):
        path="/api/recordings/example"
        self.assertEqual(self.call(path,"DELETE",headers={"Origin":ORIGIN},json={}).status_code,403)
        self.assertEqual(self.call(path,"DELETE",json=[]).status_code,400)

    def test_upload_requires_multipart_and_boundary(self):
        self.assertEqual(self.call("/api/testcase",json={}).status_code,415)
        self.assertEqual(self.call("/api/testcase",data=b"",content_type="multipart/form-data").status_code,400)

    def test_valid_multipart_reaches_existing_parser(self):
        with patch.dict(m.app.view_functions,{"upload_testcase":lambda:jsonify(ok=True)}):
            self.assertEqual(self.call("/api/testcase",data={"file":(io.BytesIO(b"fixture"),"fixture.xlsx")}).get_json(),{"ok":True})

    def test_resource_cookie_only_for_named_read_routes(self):
        path="/api/results/screenshot/missing.png"
        self.assertEqual(self.call(path,"GET",headers={}).status_code,403)
        self.call("/","GET",headers={})
        with patch.dict(m.app.view_functions,{"result_screenshot":lambda filename:jsonify(ok=True)}):
            self.assertEqual(self.call(path,"GET",headers={}).status_code,200)
        self.assertEqual(self.call("/api/runtime/status","GET",headers={}).status_code,403)

    def test_no_cross_origin_cors_grant(self):
        r=self.call("/api/input","OPTIONS",headers={"Origin":"http://evil.test"})
        self.assertEqual(r.status_code,403)
        self.assertNotIn("Access-Control-Allow-Origin",r.headers)

    def test_socket_transport_requires_origin_host_and_loopback(self):
        guard=m.local_access.wrap_socket_transport(lambda environ,start: (_ for _ in ()).throw(AssertionError("transport entered")))
        from werkzeug.test import Client
        from werkzeug.wrappers import Response
        client=Client(guard,Response)
        for path in ["/socket.io/","/socket.io/extra"]:
            for headers in [{},{"Origin":"null"},{"Origin":"http://evil.test"},{"Origin":ORIGIN,"Host":"evil.test:5000"}]:
                with self.subTest(path=path,headers=headers):
                    r=client.get(path,base_url=ORIGIN,headers=headers,environ_base={"REMOTE_ADDR":"127.0.0.1"})
                    self.assertEqual(r.status_code,403)

    def socket_client(self,auth,headers=None):
        return m.socketio.test_client(m.app,namespace="/mirror",auth=auth,
            headers=headers or {"Host":"localhost:5000","Origin":ORIGIN},flask_test_client=self.client)

    def test_socket_namespace_rejects_missing_or_forged_auth(self):
        for auth in [None,{},[],{"csrf_token":"forged"}]:
            with self.subTest(auth=auth): self.assertFalse(self.socket_client(auth).is_connected("/mirror"))

    def test_socket_namespace_rejects_bad_origin(self):
        c=self.socket_client({"csrf_token":m.local_access.token},{"Host":"localhost:5000","Origin":"null"})
        self.assertFalse(c.is_connected("/mirror"))

    def test_socket_controls_require_token_on_each_event(self):
        c=self.socket_client({"csrf_token":m.local_access.token})
        self.assertTrue(c.is_connected("/mirror"))
        try:
            with patch.object(m,"_streaming",True):
                for payload in [None,{}, {"csrf_token":"forged"}]:
                    c.emit("stop",payload,namespace="/mirror")
                    self.assertTrue(m._streaming)
                c.emit("stop",{"csrf_token":m.local_access.token},namespace="/mirror")
                self.assertFalse(m._streaming)
            with patch.object(m,"_streaming",False), patch.object(m,"check_device",return_value=False) as check:
                c.emit("start",{},namespace="/mirror")
                check.assert_not_called()
                c.emit("start",{"csrf_token":m.local_access.token},namespace="/mirror")
                check.assert_called_once()
        finally: c.disconnect(namespace="/mirror")

    def test_recording_export_memory_path_preserves_disk(self):
        with patch.object(m,"recording_export_path",return_value=__import__("pathlib").Path("not-written.xlsx")):
            payload=m.export_recording_excel({"id":"example","actions":[]},in_memory=True)
            self.assertTrue(payload.read(4).startswith(b"PK"))
