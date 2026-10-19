"""Model legitimate page requests for pre-existing regression tests."""
from flask.testing import FlaskClient
from local_access import HEADER, COOKIE, WRITE_METHODS
import local_access_config as config

class LocalBrowserClient(FlaskClient):
    def open(self, *args, **kwargs):
        import mirror_server as m
        kwargs.setdefault("base_url", "http://localhost:" + str(config.PORT))
        headers = dict(kwargs.get("headers") or {})
        headers.setdefault(HEADER, m.local_access.token)
        method = kwargs.get("method", "GET").upper()
        if method in WRITE_METHODS:
            headers.setdefault("Origin", kwargs["base_url"])
            if "json" not in kwargs and "data" not in kwargs:
                kwargs["json"] = {}
        kwargs["headers"] = headers
        return super().open(*args, **kwargs)
