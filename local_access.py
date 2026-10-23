"""Trusted network/Origin and per-process anti-forgery checks, not user authentication."""
import hmac
import ipaddress
import secrets

from flask import jsonify, request
from werkzeug.exceptions import BadRequest
from werkzeug.wrappers import Response

HEADER = "X-QA-CSRF-Token"
COOKIE = "qa_local_page"
WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
RESOURCE_ENDPOINTS = frozenset({"result_screenshot", "download_result_workbook", "recording_export"})


class LocalAccessPolicy:
    def __init__(self, address, port, hostnames, allowed_networks=()):
        if address not in {"127.0.0.1", "0.0.0.0"}:
            raise ValueError("Unsupported listener address")
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("Invalid local listener port")
        self.networks = tuple(ipaddress.ip_network(value, strict=True) for value in allowed_networks)
        private_ranges = tuple(ipaddress.ip_network(value) for value in
                               ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
        if any(net.version != 4 or not any(net.subnet_of(private) for private in private_ranges)
               for net in self.networks):
            raise ValueError("Only explicit private IPv4 networks are supported")
        if not hostnames:
            raise ValueError("Explicit browser hostnames are required")
        lan_hosts = set(hostnames) - {"localhost", "127.0.0.1"}
        for name in lan_hosts:
            if name == "::1" or not self.client_allowed(name):
                raise ValueError("LAN browser hosts must be literal IPv4 addresses in an allowed network")
        if address == "0.0.0.0" and (not self.networks or not lan_hosts):
            raise ValueError("LAN binding requires explicit networks and browser addresses")
        self.address, self.port = address, port
        suffix = "" if port == 80 else ":" + str(port)
        self.hosts = frozenset(name + suffix for name in hostnames)
        self.origins = frozenset("http://" + host for host in self.hosts)
        self.token = secrets.token_urlsafe(32)  # Never persisted; restart invalidates old pages.

    def valid_token(self, value):
        return isinstance(value, str) and value.isascii() and hmac.compare_digest(value, self.token)

    def client_allowed(self, value):
        if value in {"127.0.0.1", "::1"}:
            return True
        try:
            address = ipaddress.ip_address(value or "")
        except ValueError:
            return False
        return any(address in net and address not in {net.network_address, net.broadcast_address}
                   for net in self.networks)

    def network_error(self, environ, require_origin=False):
        if not self.client_allowed(environ.get("REMOTE_ADDR")):
            return "trusted_network_required"
        if any(key == "HTTP_FORWARDED" or key.startswith("HTTP_X_FORWARDED_") for key in environ):
            return "proxy_headers_not_supported"
        host = environ.get("HTTP_HOST", "").lower()
        if host not in self.hosts or environ.get("wsgi.url_scheme") != "http":
            return "untrusted_host"
        origin = environ.get("HTTP_ORIGIN")
        if origin is not None and origin not in self.origins:
            return "untrusted_origin"
        if require_origin and (origin is None or origin != "http://" + host):
            return "same_origin_required"
        if environ.get("HTTP_SEC_FETCH_SITE") == "cross-site":
            return "cross_site_request"
        return None

    @staticmethod
    def rejection(code, status=403):
        return jsonify(error="허용된 사내 네트워크에서 접속하세요. 정상 주소에서 화면을 새로고침해 주세요.", code=code), status

    def before_request(self):
        error = self.network_error(request.environ)
        if error:
            return self.rejection(error)
        if not request.path.startswith("/api/"):
            return None
        token_ok = self.valid_token(request.headers.get(HEADER))
        resource_ok = (request.method in {"GET", "HEAD"}
                       and request.endpoint in RESOURCE_ENDPOINTS
                       and self.valid_token(request.cookies.get(COOKIE)))
        if not token_ok and not resource_ok:
            return self.rejection("page_verification_required")
        if request.method not in WRITE_METHODS:
            return None
        error = self.network_error(request.environ, require_origin=True)
        if error:
            return self.rejection(error)
        if request.routing_exception:
            return None  # Let Flask retain 404/405 semantics for authenticated requests.
        if request.endpoint in {"upload_testcase", "app_analysis.builds", "app_analysis.references"} and request.method == "POST":
            if request.mimetype != "multipart/form-data":
                return self.rejection("multipart_required", 415)
            if not request.mimetype_params.get("boundary"):
                return self.rejection("multipart_boundary_required", 400)
            return None  # Each upload handler applies its own bounded file parser.
        if request.mimetype != "application/json":
            return self.rejection("json_required", 415)
        try:
            payload = request.get_json(silent=False)
        except BadRequest:
            return self.rejection("invalid_json", 400)
        if not isinstance(payload, dict):
            return self.rejection("json_object_required", 400)
        return None

    def after_request(self, response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        if request.path == "/" or request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def bootstrap(self, response):
        # Session-only read-resource marker. Mutations still require header + Origin.
        response.set_cookie(COOKIE, self.token, httponly=True, samesite="Strict", path="/")
        return response

    def socket_allowed(self, payload):
        return (self.network_error(request.environ, require_origin=True) is None
                and isinstance(payload, dict) and set(payload) == {"csrf_token"}
                and self.valid_token(payload.get("csrf_token")))

    def wrap_socket_transport(self, application):
        def guarded(environ, start_response):
            if (environ.get("PATH_INFO", "") == "/socket.io"
                    or environ.get("PATH_INFO", "").startswith("/socket.io/")):
                error = self.network_error(environ, require_origin=True)
                if error:
                    response = Response('{"error":"local_socket_request_rejected"}', status=403,
                                        content_type="application/json", headers={"Cache-Control": "no-store"})
                    return response(environ, start_response)
            return application(environ, start_response)
        return guarded

    def install(self, app):
        app.before_request(self.before_request)
        app.after_request(self.after_request)
        app.wsgi_app = self.wrap_socket_transport(app.wsgi_app)
