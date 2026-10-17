"""In-memory stand-ins for HubSpot, Hunter and Slack, so tests never hit the network."""
from __future__ import annotations

import itertools
import json
import re
from urllib.parse import urlparse


class FakeResponse:
    def __init__(self, status: int, body=None, headers=None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}
        self.content = b"" if body is None else json.dumps(body).encode()
        self.text = "" if body is None else json.dumps(body)

    def json(self):
        return self._body


class FakeAPI:
    def __init__(self, owners=None, companies_by_domain=None):
        self.ids = itertools.count(1001)
        self.objects = {"contacts": {}, "companies": {}, "tasks": {}}
        self.associations: list[tuple[str, str]] = []
        self.properties = {"email", "firstname", "lastname", "jobtitle", "company", "hubspot_owner_id"}
        self.owners = owners or [{"id": 1, "email": "ae-one@example.com"}, {"id": 2, "email": "ae-two@example.com"}, {"id": 3, "email": "sdr-one@example.com"}]
        self.hunter = companies_by_domain or {}
        self.slack_messages: list[str] = []
        self.calls: list[tuple[str, str]] = []
        self.fail_next: list[int] = []  # status codes to return before succeeding

    # transport interface
    def request(self, method, url, **kw):
        self.calls.append((method, url))
        if self.fail_next:
            code = self.fail_next.pop(0)
            return FakeResponse(code, {"message": "slow down"}, {"Retry-After": "0"} if code == 429 else {})
        u = urlparse(url)
        body = kw.get("json") or {}
        path = u.path

        if u.netloc == "hooks.slack.com":
            self.slack_messages.append(body["text"])
            return FakeResponse(200, None)
        if u.netloc == "api.hunter.io":
            domain = kw["params"]["domain"]
            if domain not in self.hunter:
                return FakeResponse(404, {"errors": [{"id": "not_found"}]})
            return FakeResponse(200, {"data": self.hunter[domain]})

        if method == "GET" and path == "/crm/v3/properties/contacts":
            return FakeResponse(200, {"results": [{"name": n} for n in sorted(self.properties)]})
        if method == "POST" and path == "/crm/v3/properties/contacts":
            self.properties.add(body["name"])
            return FakeResponse(201, body)
        if method == "GET" and path == "/crm/v3/owners":
            return FakeResponse(200, {"results": self.owners})

        m = re.fullmatch(r"/crm/v3/objects/(\w+)/search", path)
        if m and method == "POST":
            obj = m.group(1)
            f = body["filterGroups"][0]["filters"][0]
            hits = [
                {"id": i, "properties": p}
                for i, p in self.objects[obj].items()
                if str(p.get(f["propertyName"], "")).lower() == f["value"].lower()
            ]
            return FakeResponse(200, {"results": hits[:1]})

        if method == "POST" and path == "/crm/v3/objects/contacts/batch/read":
            res = [
                {"id": x["id"], "properties": self.objects["contacts"][x["id"]]}
                for x in body["inputs"] if x["id"] in self.objects["contacts"]
            ]
            return FakeResponse(200, {"results": res})

        m = re.fullmatch(r"/crm/v3/objects/(\w+)", path)
        if m and method == "POST":
            obj, new_id = m.group(1), str(next(self.ids))
            unknown = set(body["properties"]) - self.properties if obj == "contacts" else set()
            if unknown:
                return FakeResponse(400, {"message": f"Property values were not valid: {sorted(unknown)}"})
            self.objects[obj][new_id] = {k: str(v) for k, v in body["properties"].items()}
            return FakeResponse(201, {"id": new_id, "properties": body["properties"]})

        m = re.fullmatch(r"/crm/v3/objects/(\w+)/(\d+)", path)
        if m and method == "PATCH":
            obj, oid = m.groups()
            self.objects[obj][oid].update({k: str(v) for k, v in body["properties"].items()})
            return FakeResponse(200, {"id": oid})

        m = re.fullmatch(r"/crm/v4/objects/contacts/(\d+)/associations/default/companies/(\d+)", path)
        if m and method == "PUT":
            self.associations.append(m.groups())
            return FakeResponse(200, {"status": "COMPLETE"})

        return FakeResponse(404, {"message": f"fake has no route for {method} {path}"})
