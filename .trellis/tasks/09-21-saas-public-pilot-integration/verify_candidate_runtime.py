"""Run the recorded images against disposable, network-isolated dependencies.

No production credentials, real identity exchange, email, or model calls are used.
Each invocation retains its own redacted report and cleans up only its own Docker
resources. Existing containers, volumes, images and Git state are never modified.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

TASK = Path(__file__).resolve().parent
REPO = TASK.parents[2]
CLIENT = "ed1c238b649b6fd566967970aedd187733d828463d32484cd7199112f8d5a92d"
ISSUER = f"https://billyciallo.cloudflareaccess.com/cdn-cgi/access/sso/oidc/{CLIENT}"


class CheckFailure(Exception):
    pass


def main() -> int:
    run_id = "pilot-runtime-" + secrets.token_hex(6)
    report_path = TASK / f"{run_id}.json"
    report: dict = {
        "run_id": run_id,
        "started_at": datetime.now(UTC).isoformat(),
        "scope": "local_recorded_image_runtime",
        "real_identity_verified": False,
        "external_requests": False,
        "public_deployment": False,
        "checks": [],
    }
    owned: list[str] = []
    network_created = False
    browser_network_created = False
    label = f"docagent.pilot-runtime={run_id}"
    common = {
        "APP_ENV": "staging",
        "DATABASE__URL": "postgresql+psycopg://pilot:local-runtime-only@postgres:5432/pilot",
        "REDIS__URL": "redis://redis:6379/0",
        "OBJECT_STORE__ENDPOINT": "http://minio:9000",
        "OBJECT_STORE__PRESIGN_ENDPOINT": "http://minio:9000",
        "OBJECT_STORE__ACCESS_KEY": "local-runtime-only",
        "OBJECT_STORE__SECRET_KEY": "local-runtime-secret-only",
        "AUTH__SIGNING_KEY": secrets.token_urlsafe(48),
        "MCP__SIGNING_SECRET": secrets.token_urlsafe(48),
        "API__HOST": "0.0.0.0",
        "WORKER__HOST": "0.0.0.0",
        "MODEL__PROVIDER": "openai_compatible",
        "MODEL__BASE_URL": "https://unused-runtime-check.invalid/v1",
        "MODEL__MODEL_NAME": "runtime-check-unused",
        "MODEL__API_KEY": secrets.token_urlsafe(32),
        "EMBEDDING__PROVIDER": "openai_compatible",
        "EMBEDDING__BASE_URL": "https://unused-runtime-check.invalid/v1",
        "EMBEDDING__MODEL_NAME": "runtime-check-unused",
        "EMBEDDING__API_KEY": secrets.token_urlsafe(32),
        "BROWSER_AUTH__ENABLED": "true",
        "BROWSER_AUTH__WEB_ORIGIN": "https://agent.playlab.eu.cc",
        "BROWSER_AUTH__ISSUER": ISSUER,
        "BROWSER_AUTH__AUTHORIZATION_ENDPOINT": ISSUER + "/authorization",
        "BROWSER_AUTH__TOKEN_ENDPOINT": ISSUER + "/token",
        "BROWSER_AUTH__JWKS_URL": ISSUER + "/jwks",
        "BROWSER_AUTH__CLIENT_ID": CLIENT,
        "BROWSER_AUTH__CLIENT_SECRET": secrets.token_urlsafe(48),
        "BROWSER_AUTH__ALGORITHMS": '["RS256"]',
    }

    def command(argv: list[str], *, data: str | None = None, env=None, timeout=60):
        try:
            return subprocess.run(
                argv,
                input=data,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                env=None if env is None else {**os.environ, **env},
                cwd=REPO,
            )
        except subprocess.TimeoutExpired:
            raise CheckFailure("subprocess_timeout") from None

    def checked(argv: list[str], **kwargs) -> str:
        result = command(argv, **kwargs)
        if result.returncode:
            # Raw process output can contain runtime credentials. Retain only
            # exception class names, never arguments, messages or raw traces.
            import re

            classes = re.findall(r"(?:^|\n)([A-Za-z_.]*(?:Error|Exception)):", result.stderr)
            report["process_failure"] = {
                "exit_code": result.returncode,
                "exception_classes": classes[-3:],
            }
            raise CheckFailure("process_failed")
        return result.stdout.strip()

    def record(name: str, details: dict | None = None):
        report["checks"].append({"name": name, "status": "passed", **(details or {})})
        print(name + ": passed", flush=True)

    def run(name, image, *, args=(), environment=None, options=(), detached=False, data=None):
        full_name = run_id + "-" + name
        argv = [
            "docker",
            "create" if name == "web" else "run",
            "--pull=never",
            "--name",
            full_name,
            "--label",
            label,
            "--network",
            run_id + "-browser" if name == "web" else run_id,
        ]
        if detached and name != "web":
            argv += ["--detach"]
        if data is not None:
            argv += ["--interactive"]
        env = environment or {}
        for key in env:
            argv += ["--env", key]
        argv += [*options, image, *args]
        owned.append(full_name)
        output = checked(argv, data=data, env=env, timeout=120)
        if name == "web":
            checked(
                ["docker", "network", "connect", "--alias", "enterprise-doc-web", run_id, full_name]
            )
            checked(["docker", "start", full_name])
        return output

    def api_python(source: str) -> dict:
        output = checked(
            ["docker", "exec", "--interactive", run_id + "-api", "python", "-"], data=source
        )
        return json.loads(output)

    def wait_python(source: str, deadline=60) -> dict:
        end = time.monotonic() + deadline
        while time.monotonic() < end:
            result = command(
                ["docker", "exec", "--interactive", run_id + "-api", "python", "-"],
                data=source,
                timeout=15,
            )
            if result.returncode == 0:
                return json.loads(result.stdout)
            time.sleep(1)
        raise CheckFailure("runtime_readiness_timeout")

    active_check = "candidate_identity"
    try:
        receipt = json.loads((TASK / "release-preparation-review.json").read_text())
        images = receipt["local_images_observed"]
        for image in images.values():
            observed = json.loads(checked(["docker", "image", "inspect", image["tag"]]))[0]
            if observed["Id"] != image["image_id"]:
                raise CheckFailure("candidate_image_changed")
            if observed["Config"].get("User") in (None, "", "root", "0", "0:0"):
                raise CheckFailure("candidate_runtime_user_invalid")
        report["images"] = images
        record(active_check, {"image_count": 4})

        active_check = "isolated_dependencies"
        checked(["docker", "network", "create", "--internal", "--label", label, run_id])
        network_created = True
        run(
            "postgres",
            "pgvector/pgvector:pg16",
            detached=True,
            environment={
                "POSTGRES_USER": "pilot",
                "POSTGRES_PASSWORD": "local-runtime-only",
                "POSTGRES_DB": "pilot",
            },
            options=["--network-alias", "postgres", "--tmpfs", "/var/lib/postgresql/data:rw"],
        )
        run(
            "redis",
            "redis:7.4-alpine",
            detached=True,
            options=["--network-alias", "redis", "--tmpfs", "/data:rw"],
        )
        run(
            "minio",
            "sha256:69b2ec208575b69597784255eec6fa6a2985ee9e1a47f4411a51f7f5fdd193a9",
            detached=True,
            environment={
                "MINIO_ROOT_USER": common["OBJECT_STORE__ACCESS_KEY"],
                "MINIO_ROOT_PASSWORD": common["OBJECT_STORE__SECRET_KEY"],
            },
            options=["--network-alias", "minio", "--tmpfs", "/data:rw"],
            args=["server", "/data"],
        )
        for _ in range(40):
            ready = command(
                ["docker", "exec", run_id + "-postgres", "pg_isready", "-U", "pilot", "-d", "pilot"]
            )
            if ready.returncode == 0:
                break
            time.sleep(1)
        else:
            raise CheckFailure("database_start_timeout")
        record(active_check, {"internal_network": True, "persistent_host_data_used": False})

        for name, args in (
            ("migration", ["alembic", "upgrade", "head"]),
            ("checkpointer_setup", ["enterprise-doc-checkpointer-setup", "--setup"]),
            ("checkpointer_check", ["enterprise-doc-checkpointer-setup", "--check"]),
        ):
            active_check = name
            run(name, images["api"]["tag"], args=args, environment=common)
            record(name)

        active_check = "object_store_buckets"
        run(
            "buckets",
            images["api"]["tag"],
            environment=common,
            args=["python", "-"],
            data="""
import boto3, os
c=boto3.client('s3', endpoint_url=os.environ['OBJECT_STORE__ENDPOINT'],
    aws_access_key_id=os.environ['OBJECT_STORE__ACCESS_KEY'],
    aws_secret_access_key=os.environ['OBJECT_STORE__SECRET_KEY'], region_name='us-east-1')
for name in ('documents', 'artifacts'):
    c.create_bucket(Bucket=name)
    c.head_bucket(Bucket=name)
""",
        )
        record(active_check)

        active_check = "application_start"
        # Docker suppresses published ports when a container has only an internal
        # network. Give only the credential-free Web container a second network.
        # Browser routing below still permits requests to its loopback origin only.
        checked(["docker", "network", "create", "--label", label, run_id + "-browser"])
        browser_network_created = True
        for name in ("api", "worker", "consumer", "web"):
            opts = ["--network-alias", "enterprise-doc-" + name]
            if name == "web":
                opts += ["--publish", "127.0.0.1::8080"]
            run(
                name,
                images[name]["tag"],
                detached=True,
                environment=common if name != "web" else {},
                options=opts,
            )
        record(active_check)

        active_check = "application_readiness"
        health = wait_python("""
import json, urllib.request
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
result={}
for service, port in (('api',8000),('worker',8081),('web',8080)):
    with opener.open(f'http://enterprise-doc-{service}:{port}/health/ready',timeout=8) as r:
        body=json.load(r)
        assert r.status==200
        result[service]={'http_status':r.status,'body':body}
print(json.dumps(result))
""")
        record(active_check, health)

        active_check = "consumer_queue_connection"
        consumer = wait_python(
            """
import json
from enterprise_doc_worker.config import WorkerSettings
from enterprise_doc_worker.queue import create_celery_app
app=create_celery_app(WorkerSettings())
replies=app.control.ping(timeout=2)
assert len(replies)==1 and list(replies[0].values())[0]=={'ok':'pong'}
print(json.dumps({'responding_consumers':len(replies)}))
""",
            deadline=30,
        )
        record(active_check, consumer)

        active_check = "web_auth_routing"
        auth = api_python("""
import json, urllib.request, urllib.error
from urllib.parse import urlsplit, parse_qs
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): return None
o=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
base='http://enterprise-doc-web:8080'
with o.open(base+'/auth/session',timeout=8) as r:
    content=r.headers.get_content_type(); session=json.load(r)
    assert content=='application/json' and session=={'status':'anonymous'}
try:
    o.open(base+'/auth/login',timeout=8)
    raise AssertionError('redirect_required')
except urllib.error.HTTPError as r:
    assert r.code==303
    location=urlsplit(r.headers['Location']); q=parse_qs(location.query)
    import os
    assert location.scheme+'://'+location.netloc+location.path==os.environ['BROWSER_AUTH__AUTHORIZATION_ENDPOINT']
    assert q['client_id']==[os.environ['BROWSER_AUTH__CLIENT_ID']]
    assert q['redirect_uri']==['https://agent.playlab.eu.cc/auth/callback']
    assert q['response_type']==['code'] and q['code_challenge_method']==['S256']
    assert q.get('state') and q.get('nonce') and q.get('code_challenge')
    cookie=r.headers['Set-Cookie']
    assert 'Secure' in cookie and 'HttpOnly' in cookie and 'SameSite=lax' in cookie
with o.open(base+'/',timeout=8) as r:
    body=r.read(); assert r.headers.get_content_type()=='text/html' and b'<div id="root">' in body
print(json.dumps({'anonymous_session_json':True,'login_status':303,'exact_callback':True,'pkce':'S256','nonce_and_state_present':True,'login_cookie_flags_valid':True,'web_html_served':True,'identity_provider_contacted':False}))
""")
        record(active_check, auth)

        active_check = "browser_anonymous_render"
        port = checked(["docker", "port", run_id + "-web", "8080/tcp"])
        if not port.startswith("127.0.0.1:") or "\n" in port:
            raise CheckFailure("unexpected_browser_binding")
        screenshot = TASK / f"{run_id}-signin.png"
        browser_result = command(
            [
                "node",
                "-e",
                r"""
const {chromium}=require('./apps/web/node_modules/@playwright/test');
let phase='launch', requestFailures=[];
(async()=>{
  const browser=await chromium.launch({headless:true,
    args:['--no-proxy-server','--disable-http2','--disable-quic']});
  try {
    const context=await browser.newContext({viewport:{width:1365,height:900}});
    const origin=process.env.PILOT_BROWSER_ORIGIN;
    await context.route('**/*',route=>new URL(route.request().url()).origin===origin
      ?route.continue():route.abort());
    const page=await context.newPage();
    page.on('requestfailed',r=>{
      const code=r.failure()?.errorText;
      if(/^net::ERR_[A-Z_]+$/.test(code)) requestFailures.push(code);
    });
    let pageErrors=0, anonymousResponses=0;
    page.on('pageerror',()=>pageErrors++);
    page.on('response',r=>{
      if(new URL(r.url()).pathname==='/auth/session' && r.status()===200)
        anonymousResponses++;
    });
    phase='navigate';
    await page.goto(origin+'/#/signin',{waitUntil:'networkidle',timeout:20000});
    phase='sign_in_heading';
    await page.getByRole('heading',{name:/登录工作台|Sign in to your workspace/})
      .waitFor({timeout:15000});
    phase='login_link';
    const link=page.getByRole('link',{
      name:/使用企业账号登录|Sign in with your company account/});
    if(await link.getAttribute('href')!=='/auth/login' || pageErrors || !anonymousResponses)
      throw new Error('browser_contract_failed');
    await page.screenshot({path:process.env.PILOT_BROWSER_SCREENSHOT,fullPage:true});
    process.stdout.write(JSON.stringify({sign_in_page_rendered:true,
      login_link_correct:true,page_errors:pageErrors,session_http_200:anonymousResponses}));
  } finally {await browser.close();}
})().catch(error=>{process.stdout.write(JSON.stringify({phase,
  error_name:error.name,request_failure_codes:requestFailures}));process.exitCode=1});
""",
            ],
            env={
                "PILOT_BROWSER_ORIGIN": "http://" + port,
                "PILOT_BROWSER_SCREENSHOT": str(screenshot),
            },
            timeout=60,
        )
        browser_details = json.loads(browser_result.stdout)
        if browser_result.returncode:
            report["browser_failure"] = browser_details
            raise CheckFailure("browser_render_failed")
        record(active_check, {**browser_details, "screenshot": screenshot.name})

        active_check = "operations_packaged_preview"
        args = [
            "python",
            "-m",
            "enterprise_doc_core.operations",
            "--environment",
            "staging",
            "--database-host",
            "postgres",
            "--database-name",
            "pilot",
            "--operator",
            "candidate-runtime-check",
            "--reason",
            "Validate demo admission preview without writes",
            "admission",
            "issue",
            "--email",
            "demo-owner@example.test",
            "--expires-at",
            (datetime.now(UTC) + timedelta(days=2)).isoformat(),
            "--quota-bytes",
            "104857600",
            "--seat-limit",
            "2",
            "--credential-file",
            "/tmp/pilot-preview-must-not-exist.json",
        ]
        output = json.loads(checked(["docker", "exec", run_id + "-api", *args]))
        assert output["status"] == "preview" and output["databaseValidated"] is False
        checked(
            [
                "docker",
                "exec",
                run_id + "-api",
                "python",
                "-c",
                "from pathlib import Path; "
                "assert not Path('/tmp/pilot-preview-must-not-exist.json').exists()",
            ]
        )
        record(
            active_check,
            {
                "operation_status": "preview",
                "database_write": False,
                "credential_file_created": False,
            },
        )
        report["status"] = "passed"
    except (CheckFailure, AssertionError, KeyError, ValueError) as error:
        report["status"] = "failed"
        report["failure"] = {
            "check": active_check,
            "code": str(error) if isinstance(error, CheckFailure) else type(error).__name__,
        }
        print(f"{active_check}: failed ({report['failure']['code']})", flush=True)
    finally:
        cleanup = []
        for name in reversed(owned):
            state = command(["docker", "inspect", name])
            if state.returncode:
                continue
            obj = json.loads(state.stdout)[0]
            if obj["Config"].get("Labels", {}).get("docagent.pilot-runtime") != run_id:
                cleanup.append({"name": name, "removed": False, "reason": "ownership_mismatch"})
                continue
            if obj["State"]["Running"]:
                command(["docker", "stop", "--time", "8", name], timeout=20)
            result = command(["docker", "rm", "--volumes", name])
            cleanup.append({"name": name, "removed": result.returncode == 0})
        network_removed = not network_created
        if network_created:
            result = command(["docker", "network", "rm", run_id])
            network_removed = result.returncode == 0
        if browser_network_created:
            result = command(["docker", "network", "rm", run_id + "-browser"])
            network_removed = network_removed and result.returncode == 0
        report["cleanup"] = {"containers": cleanup, "network_removed": network_removed}
        if not network_removed or any(not entry["removed"] for entry in cleanup):
            report["status"] = "cleanup_incomplete"
        report["completed_at"] = datetime.now(UTC).isoformat()
        report["script_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        with report_path.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2)
            stream.write("\n")
        print(json.dumps({"status": report["status"], "report": str(report_path)}), flush=True)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
