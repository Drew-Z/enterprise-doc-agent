"""Deployment boundary tests; the small bundle is synthetic, not upstream code."""

import hashlib
import json
from email.parser import BytesParser
from email.policy import default
from pathlib import Path
from typing import Any

import httpx
import pytest


@pytest.fixture
def bundle(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "synthetic-bundle"
    root.mkdir()
    config_path = Path(__file__).resolve().parents[2] / "infra/cloudflare_mail/wrangler.jsonc"
    files = {
        "worker.js": b"export default {};",
        "schema.sql": b"CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);",
        "private-settings.sql": b"INSERT INTO settings VALUES ('db_version','fixture');",
        "wrangler.jsonc": config_path.read_bytes(),
        "assets/index.html": b"<html>Synthetic deployment fixture</html>",
    }
    entries = []
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        entries.append(
            {"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        )
    manifest = json.dumps({"fixture": True, "files": entries}).encode()
    (root / "manifest.json").write_bytes(manifest)
    return root, hashlib.sha256(manifest).hexdigest()


def test_same_size_tampering_is_rejected(bundle: tuple[Path, str]) -> None:
    from infra.cloudflare_mail.deploy import DeploymentError, prepare

    root, digest = bundle
    worker = root / "worker.js"
    worker.write_bytes(worker.read_bytes().replace(b"default", b"changed"))
    with pytest.raises(DeploymentError, match="Bundle file"):
        prepare(root, manifest_sha256=digest)


def test_existing_database_stops_before_any_cloud_write() -> None:
    from infra.cloudflare_mail.deploy import (
        ACCOUNT_ID,
        WORKER_NAME,
        ZONE_NAME,
        Api,
        DeploymentError,
        preflight,
    )

    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/d1/database"):
            result: object = [{"name": WORKER_NAME, "uuid": "existing-database"}]
        else:
            result = {
                "account": {"id": ACCOUNT_ID},
                "name": ZONE_NAME,
                "status": "active",
                "type": "full",
                "paused": False,
            }
        return httpx.Response(200, json={"success": True, "result": result})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        api = Api(client, "synthetic-api-token")
        with pytest.raises(DeploymentError, match="Existing database"):
            preflight(api)
    assert requests and all(request.method == "GET" for request in requests)


class FakeCloudflare:
    """HTTP boundary only: model the documented API shape, not business logic."""

    def __init__(self, *, fail_secret: bool = False) -> None:
        self.fail_secret = fail_secret
        self.requests: list[httpx.Request] = []
        self.metadata: dict[str, Any] = {}
        self.secret_names: list[str] = []
        self.deployment_id = "22222222-2222-4222-8222-222222222222"
        self.version_id = "33333333-3333-4333-8333-333333333333"
        self.script_settings: dict[str, Any] = {
            "logpush": False,
            "tail_consumers": None,
            "observability": {"enabled": False},
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:
        from infra.cloudflare_mail.deploy import ACCOUNT_ID, HOSTNAME, WORKER_NAME, ZONE_NAME

        self.requests.append(request)
        path = request.url.path
        result: object = []
        if request.method == "GET":
            if path.endswith("/script-settings"):
                result = self.script_settings
            elif path.endswith("/deployments"):
                result = {
                    "deployments": [
                        {
                            "id": self.deployment_id,
                            "versions": [{"version_id": self.version_id, "percentage": 100}],
                        }
                    ]
                }
            elif path.endswith("/versions/" + self.version_id):
                result = {
                    "id": self.version_id,
                    "resources": {
                        "script": {"etag": "fixture-etag", "handlers": ["fetch", "email"]},
                        "script_runtime": {
                            "compatibility_date": self.metadata["compatibility_date"],
                            "compatibility_flags": self.metadata["compatibility_flags"],
                            "assets": {"raw_run_worker_first": True, "serve_directly": False},
                        },
                        "bindings": self.metadata["bindings"]
                        + [{"name": name, "type": "secret_text"} for name in self.secret_names],
                    },
                }
            elif path.endswith("/subdomain"):
                result = {"enabled": False, "previews_enabled": False}
            elif "/zones/" in path and "/dns_records" not in path:
                result = {
                    "account": {"id": ACCOUNT_ID},
                    "name": ZONE_NAME,
                    "status": "active",
                    "type": "full",
                    "paused": False,
                }
        elif path.endswith("/script-settings"):
            assert request.method == "PATCH"
            self.script_settings = json.loads(request.content)
            result = self.script_settings
        elif path.endswith("/d1/database"):
            result = {
                "uuid": "11111111-1111-4111-8111-111111111111",
                "name": WORKER_NAME,
                "read_replication": {"mode": "disabled"},
            }
        elif path.endswith("/query"):
            result = [{"success": True, "results": [], "meta": {"served_by_region": "APAC"}}]
        elif path.endswith("/assets-upload-session"):
            manifest = json.loads(request.content)["manifest"]
            result = {
                "jwt": "synthetic-asset-session",
                "buckets": [[entry["hash"] for entry in manifest.values()]],
            }
        elif path.endswith("/assets/upload"):
            assert request.headers["Authorization"] == "Bearer synthetic-asset-session"
            assert request.url.params["base64"] == "true"
            result = {"jwt": "synthetic-completion-token"}
        elif path.endswith("/subdomain"):
            result = {"enabled": False, "previews_enabled": False}
        elif path.endswith("/secrets"):
            if self.fail_secret:
                return httpx.Response(
                    500,
                    json={
                        "success": False,
                        "errors": [{"code": 1000, "message": "do-not-log-" + "S" * 64}],
                    },
                )
            body = json.loads(request.content)
            self.secret_names.append(body["name"])
            result = {"name": body["name"], "type": "secret_text"}
        elif path.endswith("/workers/domains"):
            result = {"id": "synthetic-domain-id", "hostname": HOSTNAME, "service": WORKER_NAME}
        elif path.endswith("/scripts/" + WORKER_NAME):
            message = BytesParser(policy=default).parsebytes(
                b"Content-Type: "
                + request.headers["Content-Type"].encode()
                + b"\r\n\r\n"
                + request.content
            )
            for part in message.iter_parts():
                if part.get_param("name", header="content-disposition") == "metadata":
                    payload = part.get_payload(decode=True)
                    assert isinstance(payload, bytes)
                    self.metadata = json.loads(payload)
            result = {"id": WORKER_NAME, "handlers": ["fetch", "email"]}
        else:
            raise AssertionError(f"Unexpected synthetic endpoint: {request.method} {path}")
        return httpx.Response(200, json={"success": True, "result": result})


def test_failed_secret_upload_never_publishes_or_leaks_keys(
    bundle: tuple[Path, str], tmp_path: Path
) -> None:
    from infra.cloudflare_mail.deploy import Api, DeploymentError, deploy, prepare

    root, digest = bundle
    package = prepare(root, manifest_sha256=digest)
    fake = FakeCloudflare(fail_secret=True)
    credentials = {
        "PASSWORDS": json.dumps(["S" * 64]),
        "ADMIN_PASSWORDS": json.dumps(["A" * 64]),
        "JWT_SECRET": "J" * 64,
    }
    state_path = tmp_path / "deployment-state.json"
    with httpx.Client(transport=httpx.MockTransport(fake)) as client:
        with pytest.raises(DeploymentError, match="Cloudflare request failed"):
            deploy(package, Api(client, "synthetic-api-token"), credentials, state_path)
    assert not any(
        req.method == "PUT" and req.url.path.endswith("/workers/domains") for req in fake.requests
    )
    state_text = state_path.read_text()
    state = json.loads(state_text)
    assert state["status"] == "failed_needs_inspection"
    assert state["database_id"] == "11111111-1111-4111-8111-111111111111"
    assert state["steps"][-1]["status"] == "started"
    for secret in ["S" * 64, "A" * 64, "J" * 64, "synthetic-api-token", "synthetic-asset-session"]:
        assert secret not in state_text
    assert sum(req.url.path.endswith("/secrets") for req in fake.requests) == 1


def test_explicit_asset_resume_does_not_recreate_or_reinitialize_database(
    bundle: tuple[Path, str], tmp_path: Path
) -> None:
    from infra.cloudflare_mail.deploy import WORKER_NAME, Api, DeploymentError, deploy, prepare

    root, digest = bundle
    package = prepare(root, manifest_sha256=digest)
    private = {
        "PASSWORDS": json.dumps(["S" * 64]),
        "ADMIN_PASSWORDS": json.dumps(["A" * 64]),
        "JWT_SECRET": "J" * 64,
    }
    first = FakeCloudflare()

    def interrupt_upload(request: httpx.Request) -> httpx.Response:
        response = first(request)
        if request.url.path.endswith("/assets/upload"):
            raise httpx.ReadTimeout("synthetic upload interruption", request=request)
        return response

    failed_path = tmp_path / "failed.json"
    with httpx.Client(transport=httpx.MockTransport(interrupt_upload)) as client:
        with pytest.raises(DeploymentError, match="transport"):
            deploy(package, Api(client, "api-token"), private, failed_path)
    original_failure = failed_path.read_bytes()
    resumed = FakeCloudflare()

    def resume_response(request: httpx.Request) -> httpx.Response:
        result: object
        if request.method == "GET" and request.url.path.endswith("/d1/database"):
            result = [{"name": WORKER_NAME, "uuid": "11111111-1111-4111-8111-111111111111"}]
        elif request.method == "GET" and "/d1/database/" in request.url.path:
            result = {
                "name": WORKER_NAME,
                "uuid": "11111111-1111-4111-8111-111111111111",
                "read_replication": {"mode": "disabled"},
            }
        elif request.url.path.endswith("/query"):
            result = [
                {"success": True, "results": [{"mails": 0, "addresses": 0, "sent": 0}]},
                {"success": True, "results": [{"key": "db_version", "value": "fixture"}]},
            ]
        else:
            return resumed(request)
        resumed.requests.append(request)
        return httpx.Response(200, json={"success": True, "result": result})

    with httpx.Client(transport=httpx.MockTransport(resume_response)) as client:
        state = deploy(
            package,
            Api(client, "api-token"),
            private,
            tmp_path / "resumed.json",
            resume_assets_from=failed_path,
        )
    assert state["status"] == "deployed_pending_https_validation"
    assert failed_path.read_bytes() == original_failure
    assert not any(
        req.method == "POST" and req.url.path.endswith("/d1/database") for req in resumed.requests
    )
    queries = [
        json.loads(req.content)["sql"]
        for req in resumed.requests
        if req.url.path.endswith("/query")
    ]
    assert queries and all(query.startswith("SELECT") for query in queries)


def test_successful_deployment_uploads_closed_worker_before_publishing(
    bundle: tuple[Path, str], tmp_path: Path
) -> None:
    from infra.cloudflare_mail.deploy import Api, deploy, prepare

    root, digest = bundle
    fake = FakeCloudflare()
    credentials = {
        "PASSWORDS": json.dumps(["S" * 64]),
        "ADMIN_PASSWORDS": json.dumps(["A" * 64]),
        "JWT_SECRET": "J" * 64,
    }
    state_path = tmp_path / "deployment-state.json"
    with httpx.Client(transport=httpx.MockTransport(fake)) as client:
        result = deploy(
            prepare(root, manifest_sha256=digest), Api(client, "api-token"), credentials, state_path
        )
    assert result["status"] == "deployed_pending_https_validation"
    assert result["worker_verification"]["deployment_id"] == fake.deployment_id
    assert result["worker_verification"]["version_id"] == fake.version_id
    assert fake.requests[-1].url.path.endswith("/workers/domains")
    assert len(fake.secret_names) == 3
    assert not any(item["type"] == "secret_text" for item in fake.metadata["bindings"])
    assert fake.metadata["assets"]["config"]["run_worker_first"] is True
    create = next(
        req
        for req in fake.requests
        if req.method == "POST" and req.url.path.endswith("/d1/database")
    )
    assert json.loads(create.content)["read_replication"] == {"mode": "disabled"}
    assert json.loads(create.content)["primary_location_hint"] == "apac"
    state_text = state_path.read_text()
    for value in ["S" * 64, "A" * 64, "J" * 64, "api-token", "synthetic-completion-token"]:
        assert value not in state_text
    assert all(step["status"] == "succeeded" for step in result["steps"])


def test_extra_bundle_file_is_rejected(bundle: tuple[Path, str]) -> None:
    from infra.cloudflare_mail.deploy import DeploymentError, prepare

    root, digest = bundle
    (root / "extra.txt").write_text("not reviewed")
    with pytest.raises(DeploymentError, match="file set"):
        prepare(root, manifest_sha256=digest)


def test_changed_manifest_cannot_reapprove_itself(bundle: tuple[Path, str]) -> None:
    from infra.cloudflare_mail.deploy import DeploymentError, prepare

    root, digest = bundle
    (root / "manifest.json").write_text('{"files": []}')
    with pytest.raises(DeploymentError, match="manifest differs"):
        prepare(root, manifest_sha256=digest)


def test_second_page_database_conflict_still_blocks_writes() -> None:
    from infra.cloudflare_mail.deploy import (
        ACCOUNT_ID,
        WORKER_NAME,
        ZONE_NAME,
        Api,
        DeploymentError,
        preflight,
    )

    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/d1/database"):
            page = int(request.url.params["page"])
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "result": [{"name": "existing-unrelated" if page == 1 else WORKER_NAME}],
                    "result_info": {"page": page, "count": 1, "total_count": 2},
                },
            )
        return httpx.Response(
            200,
            json={
                "success": True,
                "result": {
                    "account": {"id": ACCOUNT_ID},
                    "name": ZONE_NAME,
                    "status": "active",
                    "type": "full",
                    "paused": False,
                },
            },
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(DeploymentError, match="Existing database"):
            preflight(Api(client, "api-token"))
    assert len(requests) == 3
    assert all(req.method == "GET" for req in requests)


def test_redirect_does_not_forward_credentials() -> None:
    from infra.cloudflare_mail.deploy import Api, DeploymentError

    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            302, headers={"Location": "https://example.invalid/collect"}, json={"success": False}
        )

    with httpx.Client(transport=httpx.MockTransport(respond), follow_redirects=True) as client:
        with pytest.raises(DeploymentError):
            Api(client, "api-token").request("GET", "/user/tokens/verify")
    assert len(requests) == 1 and requests[0].url.host == "api.cloudflare.com"


def test_transport_timeout_is_not_retried_or_logged_raw() -> None:
    from infra.cloudflare_mail.deploy import Api, DeploymentError

    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise httpx.ReadTimeout("raw-secret-do-not-log", request=request)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        api = Api(client, "api-token")
        with pytest.raises(DeploymentError) as error:
            api.request("POST", "/accounts/synthetic/d1/database", body={"name": "fixture"})
    assert len(requests) == 1
    assert api.events[0]["mutation_outcome_unknown"] is True
    assert "raw-secret" not in str(error.value) + json.dumps(api.events)


def test_existing_state_is_never_replayed_or_overwritten(
    bundle: tuple[Path, str], tmp_path: Path
) -> None:
    from infra.cloudflare_mail.deploy import Api, DeploymentError, deploy, prepare

    root, digest = bundle
    path = tmp_path / "state.json"
    path.write_text("existing recovery evidence")
    fake = FakeCloudflare()
    with httpx.Client(transport=httpx.MockTransport(fake)) as client:
        with pytest.raises(DeploymentError, match="State already exists"):
            deploy(prepare(root, manifest_sha256=digest), Api(client, "api-token"), {}, path)
    assert not fake.requests
    assert path.read_text() == "existing recovery evidence"


def test_unexpected_read_replication_stops_before_database_initialization(
    bundle: tuple[Path, str], tmp_path: Path
) -> None:
    from infra.cloudflare_mail.deploy import Api, DeploymentError, deploy, prepare

    fake = FakeCloudflare()

    def respond(request: httpx.Request) -> httpx.Response:
        response = fake(request)
        if request.method == "POST" and request.url.path.endswith("/d1/database"):
            payload = response.json()
            payload["result"]["read_replication"] = {"mode": "auto"}
            return httpx.Response(200, json=payload)
        return response

    root, digest = bundle
    private = {
        "PASSWORDS": json.dumps(["S" * 64]),
        "ADMIN_PASSWORDS": json.dumps(["A" * 64]),
        "JWT_SECRET": "J" * 64,
    }
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(DeploymentError, match="replication"):
            deploy(
                prepare(root, manifest_sha256=digest),
                Api(client, "api-token"),
                private,
                tmp_path / "state.json",
            )
    assert not any(request.url.path.endswith("/query") for request in fake.requests)


def test_weak_or_reused_secrets_cannot_create_resources(
    bundle: tuple[Path, str], tmp_path: Path
) -> None:
    from infra.cloudflare_mail.deploy import Api, DeploymentError, deploy, prepare

    root, digest = bundle
    private = {
        "PASSWORDS": json.dumps(["S" * 64]),
        "ADMIN_PASSWORDS": json.dumps(["S" * 64]),
        "JWT_SECRET": "J" * 64,
    }
    fake = FakeCloudflare()
    with httpx.Client(transport=httpx.MockTransport(fake)) as client:
        with pytest.raises(DeploymentError, match="distinct ASCII"):
            deploy(
                prepare(root, manifest_sha256=digest),
                Api(client, "api-token"),
                private,
                tmp_path / "state.json",
            )
    assert not fake.requests


def test_direct_asset_serving_prevents_publication(
    bundle: tuple[Path, str], tmp_path: Path
) -> None:
    from infra.cloudflare_mail.deploy import Api, DeploymentError, deploy, prepare

    fake = FakeCloudflare()

    def respond(request: httpx.Request) -> httpx.Response:
        response = fake(request)
        if request.method == "GET" and request.url.path.endswith("/versions/" + fake.version_id):
            data = response.json()
            data["result"]["resources"]["script_runtime"]["assets"]["serve_directly"] = True
            return httpx.Response(200, json=data)
        return response

    root, digest = bundle
    private = {
        "PASSWORDS": json.dumps(["S" * 64]),
        "ADMIN_PASSWORDS": json.dumps(["A" * 64]),
        "JWT_SECRET": "J" * 64,
    }
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(DeploymentError, match="assets"):
            deploy(
                prepare(root, manifest_sha256=digest),
                Api(client, "api-token"),
                private,
                tmp_path / "state.json",
            )
    assert not any(
        req.method == "PUT" and req.url.path.endswith("/workers/domains") for req in fake.requests
    )


def test_null_logging_readback_requires_explicit_disable_before_publication(
    bundle: tuple[Path, str], tmp_path: Path
) -> None:
    from infra.cloudflare_mail.deploy import Api, deploy, prepare

    fake = FakeCloudflare()
    fake.script_settings["observability"] = None

    def respond(request: httpx.Request) -> httpx.Response:
        response = fake(request)
        if request.url.path.endswith("/script-settings"):
            payload = response.json()
            payload["result"]["observability"] = None
            return httpx.Response(200, json=payload)
        return response

    root, digest = bundle
    credentials = {
        "PASSWORDS": json.dumps(["S" * 64]),
        "ADMIN_PASSWORDS": json.dumps(["A" * 64]),
        "JWT_SECRET": "J" * 64,
    }
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        state = deploy(
            prepare(root, manifest_sha256=digest),
            Api(client, "api-token"),
            credentials,
            tmp_path / "state.json",
        )
    assert state["worker_verification"]["observability_readback"] is None
    assert state["worker_verification"]["logging_disable_acknowledged"] is True
    settings_write = next(req for req in fake.requests if req.method == "PATCH")
    disabled = json.loads(settings_write.content)
    assert disabled["observability"]["enabled"] is False
    assert disabled["observability"]["logs"]["persist"] is False
    assert disabled["observability"]["traces"]["enabled"] is False


@pytest.mark.parametrize(
    "boundary",
    [
        "success",
        "changed_version",
        "domain_conflict",
        "logging_timeout",
        "split_traffic",
        "logging_still_enabled",
    ],
)
def test_existing_worker_publication_does_not_replay_resource_or_secret_writes(
    bundle: tuple[Path, str], tmp_path: Path, boundary: str
) -> None:
    from infra.cloudflare_mail.deploy import (
        WORKER_NAME,
        Api,
        DeploymentError,
        deploy,
        prepare,
        publish_existing,
    )

    root, digest = bundle
    package = prepare(root, manifest_sha256=digest)
    fake = FakeCloudflare()
    credentials = {
        "PASSWORDS": json.dumps(["S" * 64]),
        "ADMIN_PASSWORDS": json.dumps(["A" * 64]),
        "JWT_SECRET": "J" * 64,
    }

    def stop_verification(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/deployments"):
            return httpx.Response(500, json={"success": False})
        return fake(request)

    prior = tmp_path / "deployment-failed.json"
    with httpx.Client(transport=httpx.MockTransport(stop_verification)) as client:
        with pytest.raises(DeploymentError):
            deploy(package, Api(client, "api-token"), credentials, prior)
    previous_bytes = prior.read_bytes()
    fake.requests.clear()

    def existing(request: httpx.Request) -> httpx.Response:
        from infra.cloudflare_mail.deploy import HOSTNAME

        result: object
        path = request.url.path
        if (
            boundary == "domain_conflict"
            and request.method == "GET"
            and path.endswith("/workers/domains")
        ):
            result = [{"hostname": HOSTNAME, "service": "unrelated-worker"}]
        elif boundary == "logging_timeout" and request.method == "PATCH":
            fake.requests.append(request)
            raise httpx.ReadTimeout("synthetic-private-detail", request=request)
        elif boundary == "split_traffic" and path.endswith("/deployments"):
            result = {
                "deployments": [
                    {
                        "id": fake.deployment_id,
                        "versions": [
                            {"version_id": fake.version_id, "percentage": 50},
                            {
                                "version_id": "44444444-4444-4444-8444-444444444444",
                                "percentage": 50,
                            },
                        ],
                    }
                ]
            }
        elif (
            boundary == "logging_still_enabled"
            and request.method == "GET"
            and path.endswith("/script-settings")
        ):
            result = {"logpush": False, "tail_consumers": [], "observability": {"enabled": True}}
        elif request.method == "GET" and path.endswith("/d1/database"):
            result = [{"name": WORKER_NAME, "uuid": "11111111-1111-4111-8111-111111111111"}]
        elif request.method == "GET" and "/d1/database/" in path:
            result = {
                "name": WORKER_NAME,
                "uuid": "11111111-1111-4111-8111-111111111111",
                "read_replication": {"mode": "disabled"},
            }
        elif request.method == "GET" and path.endswith("/workers/scripts"):
            result = [{"id": WORKER_NAME}]
        elif path.endswith("/query"):
            result = [
                {"success": True, "results": [{"mails": 0, "addresses": 0, "sent": 0}]},
                {"success": True, "results": [{"key": "db_version", "value": "fixture"}]},
            ]
        else:
            return fake(request)
        fake.requests.append(request)
        return httpx.Response(200, json={"success": True, "result": result})

    with httpx.Client(transport=httpx.MockTransport(existing)) as client:

        def publish() -> dict[str, Any]:
            return publish_existing(
                package,
                Api(client, "api-token"),
                tmp_path / "publication.json",
                source_path=prior,
                expected_version_id=(
                    "44444444-4444-4444-8444-444444444444"
                    if boundary == "changed_version"
                    else fake.version_id
                ),
                expected_deployment_id=fake.deployment_id,
                expected_script_etag="fixture-etag",
            )

        if boundary == "success":
            state = publish()
            assert state["status"] == "deployed_pending_https_validation"
            assert state["worker_verification"]["version_id"] == fake.version_id
        else:
            with pytest.raises(DeploymentError):
                publish()
            state = json.loads((tmp_path / "publication.json").read_bytes())
            assert state["status"] == "failed_needs_inspection"
            assert "synthetic-private-detail" not in json.dumps(state)
    assert prior.read_bytes() == previous_bytes
    mutations = [req for req in fake.requests if req.method != "GET"]
    operations = [(req.method, req.url.path.rsplit("/", 1)[-1]) for req in mutations]
    if boundary == "success":
        assert operations == [("POST", "query"), ("PATCH", "script-settings"), ("PUT", "domains")]
    elif boundary == "domain_conflict":
        assert operations == []
    elif boundary in {"changed_version", "split_traffic"}:
        assert operations == [("POST", "query")]
    else:
        assert operations == [("POST", "query"), ("PATCH", "script-settings")]
    if mutations:
        assert json.loads(mutations[0].content)["sql"].startswith("SELECT")
