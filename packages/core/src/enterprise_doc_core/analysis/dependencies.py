from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from enterprise_doc_core.analysis.schemas import AnalysisField, SnapshotView, canonical_hash


@dataclass(frozen=True, slots=True)
class DependencyFingerprint:
    kind: str
    key: str
    fingerprint: str


def _field_map(snapshot: SnapshotView) -> dict[str, AnalysisField]:
    return {field.id: field for field in snapshot.payload.spec.fields}


def transitive_field_ids(snapshot: SnapshotView, field_id: str) -> tuple[str, ...]:
    fields = _field_map(snapshot)
    result: list[str] = []
    seen: set[str] = set()

    def visit(current: str) -> None:
        if current in seen:
            return
        field = fields.get(current)
        if field is None:
            return
        seen.add(current)
        for upstream in field.upstream_field_ids:
            visit(upstream)
        result.append(current)

    visit(field_id)
    return tuple(result)


def ordered_fields(snapshot: SnapshotView) -> tuple[AnalysisField, ...]:
    fields = _field_map(snapshot)
    pending = set(fields)
    result: list[AnalysisField] = []
    while pending:
        ready = sorted(
            field_id
            for field_id in pending
            if set(fields[field_id].upstream_field_ids).isdisjoint(pending)
        )
        if not ready:
            raise ValueError("field dependency cycle")
        result.extend(fields[field_id] for field_id in ready)
        pending.difference_update(ready)
    return tuple(result)


def downstream_field_ids(snapshot: SnapshotView, field_id: str) -> tuple[str, ...]:
    result = [
        field.id
        for field in snapshot.payload.spec.fields
        if field.id != field_id and field_id in transitive_field_ids(snapshot, field.id)
    ]
    return tuple(result)


def _source_hash(snapshot: SnapshotView, object_id: str | None) -> str:
    sources = [
        source.model_dump(mode="json")
        for source in snapshot.payload.sources
        if source.object_id == object_id
    ]
    return canonical_hash(
        sorted(
            sources,
            key=lambda value: (value["document_version_id"], value["generation_id"]),
        )
    )


def dependency_fingerprints(
    snapshot: SnapshotView,
    *,
    object_id: str,
    field_id: str,
    policy_hash: str,
    upstream_content_hashes: dict[str, str | None] | None = None,
) -> tuple[DependencyFingerprint, ...]:
    fields = _field_map(snapshot)
    parameters = {parameter.id: parameter for parameter in snapshot.payload.spec.parameters}
    upstream_content_hashes = upstream_content_hashes or {}
    dependencies: list[DependencyFingerprint] = []
    obj = next(item for item in snapshot.payload.spec.objects if item.id == object_id)
    dependencies.append(
        DependencyFingerprint(
            kind="object",
            key=object_id,
            fingerprint=canonical_hash({"id": obj.id, "name": obj.name}),
        )
    )
    dependencies.append(DependencyFingerprint("policy", "__policy__", policy_hash))
    for dependency_field_id in transitive_field_ids(snapshot, field_id):
        dependency_field = fields[dependency_field_id]
        dependencies.append(
            DependencyFingerprint(
                kind="field",
                key=dependency_field_id,
                fingerprint=canonical_hash(dependency_field),
            )
        )
        for parameter_id in dependency_field.parameter_ids:
            parameter = parameters[parameter_id]
            dependencies.append(
                DependencyFingerprint(
                    kind="parameter",
                    key=parameter_id,
                    fingerprint=canonical_hash(parameter),
                )
            )
        if dependency_field_id != field_id:
            dependencies.append(
                DependencyFingerprint(
                    kind="upstream_cell",
                    key=f"{object_id}:{dependency_field_id}",
                    fingerprint=upstream_content_hashes.get(dependency_field_id) or "missing",
                )
            )
    dependencies.extend(
        (
            DependencyFingerprint("object_source", object_id, _source_hash(snapshot, object_id)),
            DependencyFingerprint("common_source", "__common__", _source_hash(snapshot, None)),
        )
    )
    return tuple(sorted(dependencies, key=lambda item: (item.kind, item.key)))


def dependency_fingerprint(dependencies: Iterable[DependencyFingerprint]) -> str:
    return canonical_hash(
        [
            {"kind": item.kind, "key": item.key, "fingerprint": item.fingerprint}
            for item in sorted(dependencies, key=lambda item: (item.kind, item.key))
        ]
    )


def invalidation_reasons(
    previous: Iterable[DependencyFingerprint],
    current: Iterable[DependencyFingerprint],
) -> tuple[str, ...]:
    old = {(item.kind, item.key): item.fingerprint for item in previous}
    new = {(item.kind, item.key): item.fingerprint for item in current}
    if old == new:
        return ()
    reasons: set[str] = set()
    for kind, key in set(old) | set(new):
        if old.get((kind, key)) == new.get((kind, key)):
            continue
        if kind in {"object_source", "common_source"}:
            reasons.add("source_changed")
        elif kind == "parameter":
            reasons.add(f"parameter_changed:{key}")
        elif kind == "upstream_cell":
            reasons.add(f"upstream_changed:{key.rsplit(':', 1)[-1]}")
        elif kind == "object":
            reasons.add("object_changed")
        elif kind == "policy":
            reasons.add("policy_changed")
        else:
            reasons.add(f"field_changed:{key}")
    return tuple(sorted(reasons))
