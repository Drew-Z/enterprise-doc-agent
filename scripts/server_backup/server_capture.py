"""Consistent read-only PostgreSQL capture and bounded immutable object cache."""

import concurrent.futures
import hashlib
import json
import queue
import re
import subprocess
import threading
import time
import uuid

MAX_DUMP_BYTES = 48 * 1024 * 1024
MAX_OBJECT_BYTES = 8 * 1024 * 1024
MAX_OBJECTS = 2000
FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class CaptureError(RuntimeError):
    """Capture failed without exposing data, connection details or object names."""


def content_sql(table):
    if re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", table) is None:
        raise CaptureError("invalid source table identifier")
    value = "to_jsonb(t)"
    if table == "agent_run_evidence":
        value = (
            "((to_jsonb(t)-'rrf_score') || jsonb_build_object('rrf_score_binary',"
            "encode(float8send(t.rrf_score),'hex')))"
        )
    return (
        f"SELECT json_build_object('table','{table}','rows',count(*),'sha256',"
        """encode(sha256(convert_to(COALESCE(string_agg(row_sha,'' """
        """ORDER BY row_sha COLLATE "C"),''),'UTF8')),'hex')) FROM """
        f"(SELECT encode(sha256(convert_to({value}::text,'UTF8')),'hex') "
        f'''row_sha FROM public."{table}" t) r;'''
    )


def limited_output(command, *, env, timeout, max_bytes):
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env, creationflags=FLAGS
    )
    output = queue.Queue(maxsize=1)

    def read():
        try:
            output.put(process.stdout.read(max_bytes + 1))
        except Exception:
            output.put(None)

    worker = threading.Thread(target=read, daemon=True)
    worker.start()
    started = time.monotonic()
    try:
        data = output.get(timeout=timeout)
        if data is None or len(data) > max_bytes:
            raise CaptureError("capture process exceeded output budget")
        code = process.wait(timeout=max(0.1, timeout - (time.monotonic() - started)))
        if code != 0:
            raise CaptureError("capture process failed")
        return data
    except (queue.Empty, subprocess.TimeoutExpired):
        raise CaptureError("capture process exceeded deadline") from None
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        worker.join(timeout=5)
        if not worker.is_alive():
            process.stdout.close()


def capture_database(*, psql, pg_dump, env, extra_artifact_ids=(), timeout=180):
    if not 0 < timeout <= 240:
        raise CaptureError("invalid capture deadline")
    extra = tuple(str(uuid.UUID(value)) for value in extra_artifact_ids)
    if len(extra) > 20:
        raise CaptureError("too many explicit historical artifact references")
    process = subprocess.Popen(
        [*psql, "-X", "-qAt", "-v", "ON_ERROR_STOP=1"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=env,
        text=True,
        encoding="utf-8",
        creationflags=FLAGS,
    )
    lines = queue.Queue(maxsize=2)

    def read():
        try:
            while True:
                line = process.stdout.readline(4 * 1024 * 1024 + 1)
                lines.put(line)
                if not line or len(line) > 4 * 1024 * 1024:
                    break
        except Exception:
            lines.put("")

    worker = threading.Thread(target=read, daemon=True)
    worker.start()
    deadline = time.monotonic() + timeout

    def query(sql):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CaptureError("source transaction deadline exceeded")
        process.stdin.write(sql + "\n")
        process.stdin.flush()
        try:
            line = lines.get(timeout=min(30, remaining))
        except queue.Empty:
            raise CaptureError("source query deadline exceeded") from None
        if not line or len(line) > 4 * 1024 * 1024:
            raise CaptureError("source query failed or exceeded output budget")
        return json.loads(line)

    try:
        process.stdin.write(
            "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;\n"
            "SET LOCAL statement_timeout='30000'; "
            "SET LOCAL idle_in_transaction_session_timeout='240000';\n"
            "SET LOCAL timezone='UTC'; SET LOCAL extra_float_digits=3;\n"
        )
        process.stdin.flush()
        meta = query(
            "SELECT json_build_object('snapshot',pg_export_snapshot(),'captured_at',now(),"
            "'read_only',current_setting('transaction_read_only'),"
            "'server_version',current_setting('server_version'),"
            "'database',current_database(),'tables',(SELECT json_agg(tablename ORDER BY tablename) "
            "FROM pg_tables WHERE schemaname='public'),"
            "'extensions',(SELECT json_agg(json_build_object('name',extname,'version',extversion,"
            "'schema',nspname) ORDER BY extname) FROM pg_extension "
            "JOIN pg_namespace ON extnamespace=pg_namespace.oid),"
            "'revisions',(SELECT json_agg(version_num) FROM public.alembic_version));"
        )
        if (
            meta["read_only"] != "on"
            or meta["revisions"] != ["20260924_0031"]
            or not re.fullmatch(r"[0-9A-Fa-f-]+", meta["snapshot"])
            or not 1 <= len(meta["tables"]) <= 100
        ):
            raise CaptureError("source snapshot contract differs")
        meta["inventory"] = [query(content_sql(t)) for t in meta["tables"]]
        documents = query(
            "SELECT COALESCE(json_agg(json_build_object('reference_type','document_version',"
            "'reference_id',id,'key',object_key,'size_bytes',size_bytes,"
            "'sha256',declared_sha256) ORDER BY id),'[]'::json) FROM public.document_versions;"
        )
        include = (
            "" if not extra else " OR id IN (" + ",".join("'" + v + "'::uuid" for v in extra) + ")"
        )
        artifacts = query(
            "SELECT COALESCE(json_agg(json_build_object('reference_type','agent_artifact',"
            "'reference_id',id,'bucket',object_bucket,'key',object_key,'size_bytes',size_bytes,"
            "'sha256',content_sha256) ORDER BY id),'[]'::json) "
            "FROM public.agent_artifacts WHERE status IN ('draft_ready','published','revoked')"
            + include
            + ";"
        )
        invalid = query(
            "SELECT to_json(count(*)) FROM public.document_versions v "
            "LEFT JOIN public.upload_sessions u ON u.id=v.upload_session_id "
            "WHERE u.id IS NULL OR v.object_key IS DISTINCT FROM u.object_key;"
        )
        if invalid != 0:
            raise CaptureError("document references do not match upload records")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CaptureError("source capture deadline exceeded before dump")
        data = limited_output(
            [
                *pg_dump,
                "--format=custom",
                "--no-owner",
                "--no-privileges",
                "--schema=public",
                "--strict-names",
                "--lock-wait-timeout=10000",
                "--snapshot=" + meta["snapshot"],
            ],
            env=env,
            timeout=remaining,
            max_bytes=MAX_DUMP_BYTES,
        )
        if not data.startswith(b"PGDMP"):
            raise CaptureError("source did not produce a PostgreSQL custom archive")
        meta.update(dump_sha256=hashlib.sha256(data).hexdigest(), dump_size_bytes=len(data))
        return {"database": data, "inventory": meta, "references": documents + artifacts}
    except CaptureError:
        raise
    except Exception:
        raise CaptureError("consistent source capture did not complete") from None
    finally:
        try:
            process.stdin.write("ROLLBACK;\n\\q\n")
            process.stdin.flush()
        except (BrokenPipeError, OSError):
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        worker.join(timeout=5)
        process.stdin.close()
        if not worker.is_alive():
            process.stdout.close()


def capture_objects(*, client, references, documents_bucket, allowed_buckets, cache, timeout=120):
    deadline = time.monotonic() + timeout
    if not 0 < timeout <= 180 or not 1 <= len(references) <= MAX_OBJECTS:
        raise CaptureError("object budget exceeded")
    records = []
    identities = set()
    locations = set()
    total = 0
    for ref in references:
        item = dict(ref)
        if item["reference_type"] == "document_version":
            item["bucket"] = documents_bucket
        elif item["reference_type"] != "agent_artifact":
            raise CaptureError("unsupported object reference")
        identity = (item["reference_type"], item["reference_id"])
        location = (item["bucket"], item["key"])
        if (
            identity in identities
            or location in locations
            or item["bucket"] not in allowed_buckets
            or not isinstance(item["key"], str)
            or not item["key"]
            or type(item["size_bytes"]) is not int
            or not 0 <= item["size_bytes"] <= MAX_OBJECT_BYTES
            or not isinstance(item["sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
        ):
            raise CaptureError("invalid or duplicate source object reference")
        identities.add(identity)
        locations.add(location)
        total += item["size_bytes"]
        if total > MAX_OBJECT_BYTES:
            raise CaptureError("object memory budget exceeded")
        item["bundle_member"] = (
            "objects/"
            + hashlib.sha256((item["bucket"] + "\0" + item["key"]).encode()).hexdigest()
            + ".blob"
        )
        records.append(item)

    def get(item):
        if time.monotonic() > deadline:
            raise CaptureError("object capture deadline exceeded")
        identity = (item["bucket"], item["key"], item["sha256"], item["size_bytes"])
        prior = cache.get(identity)
        if (
            prior is not None
            and len(prior) == item["size_bytes"]
            and hashlib.sha256(prior).hexdigest() == item["sha256"]
        ):
            return item, prior, True
        response = client.get_object(Bucket=item["bucket"], Key=item["key"])
        body = response["Body"]
        try:
            if response.get("ContentLength") != item["size_bytes"]:
                raise CaptureError("source object length differs")
            data = body.read(item["size_bytes"] + 1)
        finally:
            body.close()
        if len(data) != item["size_bytes"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise RuntimeError("source object integrity differs")
        return item, data, False

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(get, records))
    except Exception:
        raise CaptureError("source object capture did not complete") from None
    if time.monotonic() > deadline:
        raise CaptureError("object capture deadline exceeded")
    # Replace the cache only after the whole snapshot passes; no plaintext files.
    cache.clear()
    payload = {}
    for item, data, _ in results:
        cache[item["bucket"], item["key"], item["sha256"], item["size_bytes"]] = data
        payload[item["bundle_member"]] = data
    payload["objects.json"] = json.dumps({"objects": records}, sort_keys=True).encode()
    return {
        "payload": payload,
        "objects": len(records),
        "object_bytes": total,
        "source_gets": sum(not reused for _, _, reused in results),
    }
