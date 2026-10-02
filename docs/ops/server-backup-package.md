# Server backup operations package

The tracked implementation lives in `scripts/server_backup`. It includes consistent
read-only capture, age encryption, immutable upload/readback, durable same-ID retry,
remote byte admission, retention planning, an operator restore catalog, strict production configuration and
first-installation/rollback helpers. It has not been installed on production.

The original private implementations, failed attempts and ciphertexts remain in
the centralized task recovery group. Promotion keeps SQL behavior intact: three
generated fingerprint queries and all five capture query expressions match the
frozen implementation. The packaged crypto module passes the three real-age tests.

## Package preparation

`deployment.prepare_package` reads the exact Python module allowlist and an
explicit, SHA-256-verified Linux age ELF binary. Its inventory binds every byte and
size to a package ID. It returns the immutable file set, public configuration and
inventory; it creates no credentials, connects to no provider and writes no files.
Official age v1.3.2 remains pinned. The verified Linux binary is
`eb7dd1b518f0a307c99cd97782623c5321da049154b04acd2d98d21aa7bc9b2c`.

The production configuration fixes the source namespace to
`enterprise-doc-agent-staging`, the target prefix to `operations-recovery/v1/`,
the interval to 60–300 seconds and the managed-prefix cap to at most 3 GiB.
It accepts only a native age public recipient and a credential-free HTTPS R2
account endpoint. Source/target credential commands are fixed argument arrays;
shell substitutions, arbitrary commands and validation/crash-test fields are
rejected. Carry forward the explicit historical artifact references established
by the actual recovery inventory when preparing the real production configuration.

The default-jurisdiction R2 endpoint is exactly
`https://<account-id>.r2.cloudflarestorage.com`. The original synthetic installation
fixture incorrectly used `.r2.storage.cloudflare.com`; a new regression rejects
that typo and accepts the official endpoint. Local DNS failed for the former and
resolved the latter. Preserve the earlier installation receipt as filesystem/unit
validation only; it never tested target connectivity. Existing bucket metadata
confirms the proposed backup bucket uses the default jurisdiction. Production
source and backup target use the same R2 account authority and different bucket
names: this is not an independent provider/account failure domain.

Official [temporary credential documentation](https://developers.cloudflare.com/r2/api/s3/temporary-credentials/)
supports locally signed credentials restricted to a bucket, prefix and explicit
S3 actions. The proposed publisher actions are `ListObjectsV2`, `HeadObject`,
`GetObject`, and `PutObject` under `operations-recovery/v1/`; deletion is excluded.
The parent must have only the [bucket item permission](https://developers.cloudflare.com/r2/api/tokens/)
for the selected bucket. `target_credentials.publication_credentials` now signs
a fresh 900-second session on every publication, including resumed uploads. It
uses HS256 with the UTF-8 parent secret string, derives the child secret from the
compact JWT's SHA-256, and supplies the base64 `jwt/` envelope as the SDK session
token. The parent secret stays in the root-owned file and signer memory.

Production publication rejects absent, expired, overbroad or misbound sessions
before constructing the S3 client; it does not fall back to the parent key. The
local claim validator does not authenticate the JWT signature: R2 does that and
enforces delegated permissions. Root still holds the parent, so this design does
not isolate a compromised host root. Parent revocation invalidates child sessions.

Independent PyJWT verification and actual botocore SigV4 signing pass. The same
adapter/SDK code also ran in the local Linux VM with a synthetic protected file:
mode 0644 was rejected, mode 0600 accepted, the parent secret was not returned,
and the owned synthetic file was removed. There were no target network requests.
Seventeen new credential tests plus the existing backup/documentation suite pass
(81 tests). See the [credential evidence](../../.trellis/tasks/09-24-commercial-operations-acceptance/server-backup-credential-validation.json).

The account operator subsequently provisioned a dedicated parent through the local
DPAPI input tool. Independent API readback confirmed one active bucket item-write
grant for exactly the target bucket, with no account-wide grant. The original
operations token was not installed as the parent.

Live R2 rejected the vendor example's simultaneous `scope` and `actions` claims
with `InvalidArgument / X-Amz-Security-Token`. An API-issued read-only token and
its locally re-signed equivalent worked; adding `actions` reproduced the rejection,
while action-only signing worked. The signer and consumer now use only the fixed
four actions, without a broader `scope` preset. The regression failed before this
correction and passed afterward.

The corrected token passed real immutable publication/readback, HEAD/LIST,
same-identity conditional replay and renewal. Delete and out-of-prefix read/write/
listing were denied with 403; an expired session was also rejected. Only a
247-byte encrypted synthetic payload and its marker were uploaded. Initial
cleanup hit local proxy/TLS errors, so that overall attempt remains failed; a
separate cleanup verified the original hashes, deleted exactly those two objects
and confirmed absence. The first malformed-session attempt wrote no objects.
Native Linux validation was repeated against the corrected module hashes.
See the [live credential evidence](../../.trellis/tasks/09-24-commercial-operations-acceptance/server-backup-live-credential-validation.json).

Parent credentials remain DPAPI-protected on the personal computer. Production
installation, protected server provisioning, monitoring and off-computer backup
continuity remain open; no sensitive database/document backup was uploaded.

| Path | Purpose |
| --- | --- |
| `/var/lib/enterprise-doc-backup/releases/<package-id>/` | Verified code and Linux age binary |
| `/var/lib/enterprise-doc-backup/config.json` | Public runtime configuration, root-owned and private |
| `/var/lib/enterprise-doc-backup/target.json` | Separately provisioned bucket-scoped parent S3 credentials, root-owned mode 0600 |
| `/var/lib/enterprise-doc-backup/state/` | Durable attempt state and bounded encrypted upload spool |
| `/etc/systemd/system/enterprise-doc-backup.service` | Owned service unit |

The source adapter reads only the selected database/object-store environment fields
from the existing API deployment. The daemon consumes that JSON in memory. The
target adapter requires exactly endpoint, bucket, access, secret and region fields;
access/secret are 32/64 lowercase hex characters and region is `auto`. It returns
a temporary child, and rejects another file path or additional admin credentials. Protected JSON reads
reject symlinks, non-regular files, non-root ownership, group/world permissions and
oversized input. Do not run the credential adapter directly in an interactive log.

## First installation and rollback

`deployment.install_first` verifies the inventory and config before filesystem
writes. It refuses an existing root or unit, uses exclusive file creation, reloads
systemd and returns `staged_not_started`. It neither creates `target.json` nor
enables/starts the service. Failed partial installations remain available for
inspection; they are not overwritten by rerunning the installer.

The unit uses `--require-production-config`, private file permissions, a 640 MiB
memory cap, 150% CPU quota, a 600-second watchdog and control-group termination.
Only the state directory is writable to the running service. The unit has no
validation cycle limit or injected crash probe.

`deployment.stop_owned_installation` verifies the recorded unit hash before
disabling/stopping that service. It deletes no code, config, credentials, state or
backups. The original Windows queue/backup tasks remain outside its scope.

Native preflight confirmed Python 3.12.3, PostgreSQL clients 17.10, systemd 255,
`/usr/local/bin/k3s` and five ready application deployments on the existing host.
No production service or backup data was written during that preflight.

## Local native validation and limits

The actual package was staged in the existing local QEMU Linux VM using the
production filesystem/unit paths. `systemd-analyze verify` passed, the daemon
loaded the strict production config, and installed bytes matched the manifest.
An intentionally mode-0644 synthetic target file was rejected; mode 0600 was
accepted, then that task-owned synthetic file was removed.

Rollback left the unit inactive and disabled, with an empty state directory.
The local package and bootstrap kit remain for follow-up validation. The target
was synthetic; no source or target network call, capture, supplier request or
production upload occurred. No private age identity was copied to the VM.

Forty-two repository tests cover runtime retry, publication, retention, config
drift, package tampering, existing-target refusal, first installation and rollback.
Three real-age tests separately cover roundtrip, wrong keys, corrupted ciphertext
and invalid archives. See the [package evidence](../../.trellis/tasks/09-24-commercial-operations-acceptance/server-backup-package-validation.json).

## Operator restore and protection catalog

`restore_catalog.inspect_snapshot` authenticates a local age bundle and verifies
its dump binding, table inventory and every object mapping/byte hash. Logical
content identity includes table fingerprints, object locations/references/hashes,
schema revision/extensions and release metadata. Capture timestamps, randomized
ciphertext and incidental dump bytes do not create distinct logical contents.

`record_restore` imports the original executed verifier's receipt with independently
pinned receipt and verifier hashes from the protected local recovery registry.
These hashes establish evidence identity; the operator must still establish that
the verifier actually executed. Upload markers cannot provide these trust anchors.
Successful receipts must bind the same ciphertext/dump and demonstrate complete
table fingerprints, object readback/inventory, ordered timestamps and owned cleanup.
Failed or unknown statuses remain preserved and protected even if another attempt
for the same snapshot succeeded. A supplemental audit cannot rewrite an original failure.

`build_catalog` includes explicit owner-to-snapshot references, pinned identities
and in-progress identities. Store the returned document exclusively in the local
recovery group and pin its exact byte SHA in the locked registry. Pass those bytes
and that independent SHA to `plan_from_catalog`; it revalidates the catalog before
calling the existing read-only planner. Missing/unverified snapshots remain protected,
including owner references to snapshots absent from remote inventory. The catalog
is operator-maintained; it does not discover every external owner automatically.
No catalog API uploads, deletes, or grants retention approval.

Real local validation authenticated all three existing native ciphertexts:
56 tables/82,705 rows and 1,669 objects each. Three ciphertext hashes represented
one logical content. All three remain protected, with zero actual-restore proofs:
two have no restore receipt and the third retains `original_container_state_changed`.
The original receipt and lifecycle audit were not modified; no restore was rerun.
Sixteen catalog tests plus the previous 42 backup tests pass, including the real
planner with only S3 transport replaced. See the [catalog evidence](../../.trellis/tasks/09-24-commercial-operations-acceptance/server-backup-catalog-validation.json).

Adding this module changes the package inventory/ID. The earlier native staged
package proof applies only to its original bytes; the new candidate has been
prepared/verified locally and has not replaced the staged local unit or production.

Remaining work is the actual target/bucket credential package, ongoing operator
catalog maintenance, approved retention execution, the specific remote
sensitive-backup storage exception, production activation/monitor configuration,
and continuous operation while the personal computer is offline. This local
installation check does not replace those requirements or the other acceptance gates.
