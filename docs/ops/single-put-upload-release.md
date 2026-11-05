# Small-file single PUT release

Status: rc.24 deployed and verified on schema0032 (2026-10-04 23:37 UTC),
source `d14fa62f222f656b6155ec71b401453ef7a71082`. Single PUT creation was enabled
by the guarded same-image configuration switch (91.84 seconds). Public browser
normal, lost-response412 retry, and refresh/reselect recovery passed. The normal
41-byte upload sample took8.01 seconds; this is not a percentile or capacity result.
Five synthetic sessions completed with zero reserved bytes and distinct versions.
The existing demonstration tenant had no processing allowance, so these documents
failed subsequent ingestion; full business/performance acceptance remains pending.
The first two browser harness runs incorrectly waited for a local-only result element;
their failures are retained, and the corrected test checks the visible completion state.
Four application specifications and 20 prerequisites match the exact candidate;
public homepage/readiness return200. A containerd alias issue initially prevented
migration container startup; adding four equivalent qualified aliases resolved it.
The existing migration Job then completed once, without restoring old images.

Files up to1MiB may upload with one conditional object PUT instead of multipart
creation/part upload/completion. The API still verifies ownership, full SHA-256,
length and file envelope before linking the document and converting reserved quota.
This reduces required object-store round trips; public latency improvement remains
unproven until a new production measurement.

## Compatibility and recovery

- API callers omitting transport retain multipart. New browser clients request
  single_put for small files and follow the server's returned transport.
- `UPLOAD__SINGLE_PUT_ENABLED=false` affects only new sessions. Existing idempotency
  keys replay their stored transport and quota reservation across switch changes.
- Browser recovery records retain the transport. A repeated PUT412 requires an
  authoritative read and matching hash/size/ETag before completion.
- Cancellation releases quota once. Conditional zero-byte retirement markers prevent
  delayed signed PUTs recreating canceled content. Keep these markers, including after
  demo cleanup; they are not backups or retention-deletion candidates.

## Release sequence

1. Verify candidate CI, signed images and the exact manifest. Capture current resource
   identities in the existing centralized recovery group.
2. Render with `STAGING_UPLOAD_SINGLE_PUT_ENABLED=false`. Use maintenance plan
   schema3, `original_revision=20260924_0031`, `target_revision=20261005_0032`.
   The only configuration addition permitted by this plan is the disabled flag;
   all other settings must match the captured baseline. Bind the full candidate hash.
3. Use the existing maintenance supervisor and atomic migration claim. Before the
   claim, bounded restoration of original services remains available. After claim,
   never restore old images automatically, even if migration outcome is unknown.
4. Apply0032 and the compatible API/worker/consumer/web build, then verify the
   disabled baseline. Resolve an uncertain migration with candidate forward recovery.
5. Enable via the0032 configuration-only release plan. It requires explicit old/new
   boolean values, identical four application images and unchanged credentials/pool.
   The same mechanism can disable new direct sessions while preserving existing ones.
6. Run fresh public upload, recovery, quota and performance tests. Supplier generation,
   business quality, capacity and final user acceptance remain separate requirements.

Schema downgrade refuses any single_put history under an exclusive table lock.
Do not delete history or relabel sessions to bypass the check. rc.23 is not an
unconditional fallback after activation. The original schema1/0027 maintenance plan
remains separate and must not be repurposed.

## Validation boundaries

Local validation includes isolated PostgreSQL migrations and upload/cleanup tests,
full frontend tests and real Chromium/API/MinIO normal, lost-response412 and refresh
recovery. A separate real R2 probe used the candidate signer and browser XHR from the
allowed production origin: PUT200, exposed ETag, repeated412 and unchanged bytes;
the synthetic object was verified and deleted.

R2 CORS now permits the five required conditional/metadata headers only for the
existing application origin. The previous configuration is retained in the recovery
group. Guard tests use simulated Kubernetes boundaries plus real SQLite fencing;
they do not establish that a production rollout has occurred.

Continuous backups and additional restoration qualification remain deferred under
the user's launch scope. This feature does not restart them or mark them passed.
