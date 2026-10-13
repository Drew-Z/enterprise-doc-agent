"""Validate a frozen plan or execute it against an explicitly controlled local target."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.engine import make_url

from enterprise_doc_api.auth.jwt import DatabasePrincipalResolver, JwtTokenCodec
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_api.tenant_usage.router import TenantUsageResponse
from enterprise_doc_core.config import AppEnvironment, EmbeddingProviderKind, ModelProvider
from enterprise_doc_core.db import create_session_factory, selector_event_loop_factory
from enterprise_doc_core.documents import HashEmbeddingProvider
from enterprise_doc_core.documents.models import DocumentVersion
from enterprise_doc_core.documents.retrieval_service import HybridRetrievalService
from enterprise_doc_core.health.adapters import FoundationResources, build_foundation_resources
from enterprise_doc_core.presales.models import PresalesPacket
from scripts.business_capacity import (
    BusinessFailure,
    BusinessIO,
    LoadedBusinessPlan,
    decode,
    load_business_plan,
    loopback_origin,
    run_business_matrix,
)
from scripts.business_capacity_observer import CoreBusinessObserver

ROOT = Path(__file__).resolve().parents[1]


def validate_output(path: Path) -> Path:
    resolved = path.resolve()
    if (
        not path.is_absolute()
        or resolved.exists()
        or resolved.is_relative_to(ROOT)
        or not resolved.parent.is_dir()
    ):
        raise ValueError("output_directory_rejected")
    return resolved


def validate_local_settings(settings: ApiSettings, loaded: LoadedBusinessPlan) -> None:
    if (
        settings.app_env not in {AppEnvironment.LOCAL, AppEnvironment.TEST}
        or settings.embedding.provider != EmbeddingProviderKind.HASH
        or settings.model.provider != ModelProvider.DETERMINISTIC
        or settings.model.fallback_provider not in {None, ModelProvider.DETERMINISTIC}
        or make_url(settings.database.url.get_secret_value()).host
        not in {"127.0.0.1", "localhost", "::1"}
        or urlsplit(settings.redis.url.get_secret_value()).hostname
        not in {"127.0.0.1", "localhost", "::1"}
    ):
        raise ValueError("controlled_local_settings_required")
    loopback_origin(settings.object_store.endpoint)
    origin = loopback_origin(settings.object_store.presign_endpoint)
    if origin not in {loopback_origin(v) for v in loaded.plan.object_origins}:
        raise ValueError("object_origin_mismatch")


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


async def execute_local(
    loaded: LoadedBusinessPlan, output: Path, settings: ApiSettings
) -> dict[str, Any]:
    output = validate_output(output)
    validate_local_settings(settings, loaded)
    token = os.environ.get(loaded.plan.token_env)
    if not token:
        raise ValueError("local_token_required")
    revision = await asyncio.to_thread(
        subprocess.run,
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    head = revision.stdout.strip()
    worktree = await asyncio.to_thread(
        subprocess.run,
        ["git", "status", "--porcelain"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        timeout=10,
    )
    dirty = bool(worktree.stdout)
    output.mkdir()  # Exclusive creation before any service connection.
    base = {
        "schema_version": 1,
        "scope": "local-controlled-business-sampler",
        "status": "running",
        "production_capacity_approved": False,
        "plan_sha256": loaded.sha256,
        "planned_tasks": loaded.plan.total_tasks,
        "preflight_completed": False,
        "executor_commit_sha": head,
        "executor_worktree_dirty": dirty,
        "source_hashes": {
            name: hashlib.sha256((ROOT / "scripts" / name).read_bytes()).hexdigest()
            for name in (
                "business_capacity.py",
                "business_capacity_observer.py",
                "run_business_capacity.py",
            )
        },
        "controlled_target_operator_confirmed": True,
        "started_at": datetime.now(UTC).isoformat(),
    }
    write_json(output / "run.json", base)
    write_json(output / "plan.json", loaded.plan.model_dump(mode="json"))
    resources: FoundationResources | None = None
    try:
        resources = build_foundation_resources(settings)
        sessions = create_session_factory(resources.database_engine)
        async with asyncio.timeout(loaded.plan.max_run_seconds + 15):
            principal = await DatabasePrincipalResolver(
                session_factory=sessions, codec=JwtTokenCodec(settings.auth)
            ).resolve(token)
            if principal.role != "owner":
                raise BusinessFailure("isolated_owner_required")
            tenant_id = UUID(principal.tenant_id)
            async with sessions() as session:
                has_documents = await session.scalar(
                    select(DocumentVersion.id)
                    .where(DocumentVersion.tenant_id == tenant_id)
                    .limit(1)
                )
                has_packets = await session.scalar(
                    select(PresalesPacket.id).where(PresalesPacket.tenant_id == tenant_id).limit(1)
                )
                if has_documents or has_packets:
                    raise BusinessFailure("empty_isolated_tenant_required")
            io = BusinessIO(loaded.plan, token)
            async with io.client() as client:
                usage = decode(
                    TenantUsageResponse, await io.request(client, "GET", "/api/tenant-usage")
                )
            if (
                usage.tenant_id != tenant_id
                or usage.entitlement_status != "active"
                or usage.provider_requests_remaining is None
                or usage.provider_requests_remaining < loaded.plan.total_tasks
                or not any(
                    q.metric == "document_bytes"
                    and q.remaining
                    >= sum(
                        loaded.cases[i % len(loaded.cases)].spec.size_bytes
                        for i in range(loaded.plan.total_tasks)
                    )
                    for q in usage.product_quotas
                )
            ):
                raise BusinessFailure("isolated_quota_preflight_failed")
            observer = CoreBusinessObserver(
                sessions,
                resources.multipart_object_store,
                settings.object_store.documents_bucket,
                HybridRetrievalService(
                    session_factory=sessions, embedding_provider=HashEmbeddingProvider()
                ),
                principal,
            )
            base["preflight_completed"] = True
            with (output / "samples.jsonl").open("x", encoding="utf-8", newline="\n") as journal:

                def record(sample: dict[str, Any]) -> None:
                    journal.write(json.dumps(sample, ensure_ascii=False) + "\n")
                    journal.flush()

                result = await run_business_matrix(loaded, token, observer, io=io, on_sample=record)
            base.update(result)
    except asyncio.CancelledError:
        base.update(status="interrupted", error_code="run_cancelled")
    except Exception as error:
        base.update(
            status="interrupted",
            error_code=str(error)
            if isinstance(error, BusinessFailure)
            else "local_execution_failed",
            error_type=type(error).__name__,
        )
    finally:
        if resources is not None:
            try:
                async with asyncio.timeout(15):
                    await resources.close()
                base["local_clients_closed"] = True
            except (Exception, asyncio.CancelledError) as error:
                base.update(
                    status="interrupted",
                    local_clients_closed=False,
                    close_error_type=type(error).__name__,
                )
        if not base["preflight_completed"]:
            base["tasks_not_started"] = loaded.plan.total_tasks
        base["completed_at"] = datetime.now(UTC).isoformat()
        write_json(output / "run.json", base)
    return base


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--execute-local", action="store_true")
    parser.add_argument("--confirm-controlled-target", action="store_true")
    args = parser.parse_args()
    try:
        loaded = load_business_plan(args.plan)
        if not args.execute_local:
            print(
                json.dumps(
                    {
                        "dry_run": True,
                        "plan_sha256": loaded.sha256,
                        "planned_tasks": loaded.plan.total_tasks,
                        "max_http_requests": loaded.plan.max_http_requests,
                        "production_capacity_approved": False,
                    }
                )
            )
            return
        if args.output_dir is None:
            raise ValueError("explicit_output_directory_required")
        validate_output(args.output_dir)
        if not args.confirm_controlled_target:
            raise ValueError("controlled_target_confirmation_required")
        result = asyncio.run(
            execute_local(loaded, args.output_dir, ApiSettings(_env_file=None)),
            loop_factory=selector_event_loop_factory,
        )
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        codes = {
            "output_directory_rejected",
            "explicit_output_directory_required",
            "controlled_target_confirmation_required",
            "controlled_local_settings_required",
            "object_origin_mismatch",
            "local_token_required",
            "fixture_integrity",
            "fixture_path",
            "plan_too_large",
            "invalid_business_plan",
        }
        parser.exit(
            2,
            (
                str(error)
                if type(error) is ValueError and str(error) in codes
                else "configuration_rejected"
            )
            + "\n",
        )
    print(
        json.dumps(
            {
                "status": result["status"],
                "report": str(args.output_dir / "run.json"),
                "production_capacity_approved": False,
            }
        )
    )
    if result["status"] != "local_checks_passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
