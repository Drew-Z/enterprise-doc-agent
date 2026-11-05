from __future__ import annotations

import hashlib

from enterprise_doc_core.config import ModelProvider
from enterprise_doc_core.presales.errors import PresalesError
from enterprise_doc_core.presales.gateway import OpenAICompatiblePresalesGateway, PresalesGateway
from enterprise_doc_core.presales.policy import (
    DEEP_MODEL_SECONDS,
    DEEP_ROW_SECONDS,
    ExecutionMode,
    ExecutionPolicy,
    RouteName,
    RoutePolicy,
)
from enterprise_doc_core.presales.settings import PresalesSettings


def endpoint_digest(gateway: OpenAICompatiblePresalesGateway) -> str:
    return hashlib.sha256((gateway.settings.base_url or "").encode()).hexdigest()


def freeze_policy(
    gateway: PresalesGateway,
    settings: PresalesSettings,
    mode: ExecutionMode,
    *,
    background: bool,
) -> ExecutionPolicy:
    if mode == "deep" and not background:
        raise PresalesError("presales_background_required")
    if not isinstance(gateway, OpenAICompatiblePresalesGateway):
        raise PresalesError("presales_execution_policy_unavailable")
    order: list[RouteName] = [settings.model_route]
    if background and settings.automatic_failover_enabled:
        order.append("fallback" if order[0] == "primary" else "primary")
    routes = []
    for name in order:
        try:
            selected = OpenAICompatiblePresalesGateway(
                gateway.source_settings,
                presales_settings=settings.model_copy(update={"model_route": name}),
            )
        except ValueError as error:
            raise PresalesError("presales_execution_policy_unavailable") from error
        model = selected.settings
        if model.provider is not ModelProvider.OPENAI_COMPATIBLE or model.model_name is None:
            raise PresalesError("presales_execution_policy_unavailable")
        routes.append(
            RoutePolicy(
                route=name,
                provider="openai_compatible",
                endpoint_sha256=endpoint_digest(selected),
                model_name=model.model_name,
                model_version=model.model_version,
                model_revision=model.model_revision,
                reasoning_effort=("xhigh" if model.reasoning_effort == "xhigh" else "high")
                if mode == "deep"
                else model.reasoning_effort,
                streaming=model.streaming,
                timeout_seconds=DEEP_MODEL_SECONDS
                if mode == "deep"
                else selected.request_timeout_seconds,
                max_output_bytes=model.max_output_bytes,
                prompt_version=selected.provenance["promptVersion"],
                prompt_sha256=selected.provenance["promptSha256"],
            )
        )
    return ExecutionPolicy(
        mode=mode,
        row_timeout_seconds=max(settings.row_timeout_seconds, DEEP_ROW_SECONDS)
        if mode == "deep"
        else settings.row_timeout_seconds,
        queue_timeout_seconds=settings.queue_timeout_seconds,
        max_provider_requests=len(routes),
        daily_dispatch_limit=settings.daily_dispatch_limit,
        routes=routes,
    )


def restore_gateway(template: PresalesGateway, policy: RoutePolicy) -> PresalesGateway:
    if (
        not isinstance(template, OpenAICompatiblePresalesGateway)
        or template.model_provider != policy.provider
        or endpoint_digest(template) != policy.endpoint_sha256
        or template.provenance.get("promptVersion") != policy.prompt_version
        or template.provenance.get("promptSha256") != policy.prompt_sha256
    ):
        raise PresalesError("presales_execution_policy_unavailable")
    # Credentials stay in the process and remain bound to their original endpoint.
    return OpenAICompatiblePresalesGateway(
        template.settings.model_copy(
            update={
                "model_name": policy.model_name,
                "model_version": policy.model_version,
                "model_revision": policy.model_revision,
                "reasoning_effort": policy.reasoning_effort,
                "streaming": policy.streaming,
                "timeout_seconds": policy.timeout_seconds,
                "route_deadline_seconds": policy.timeout_seconds,
                "max_output_bytes": policy.max_output_bytes,
            }
        ),
        transport=template.transport,
        strict_output=template.strict_output,
        question_assessment=template.question_assessment,
        question_prompt_version=template.question_prompt_version,
    )
