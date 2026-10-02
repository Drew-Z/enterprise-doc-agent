# Server backup operations package

The tracked implementation lives in `scripts/server_backup`. It includes consistent
read-only capture, age encryption, immutable upload/readback, durable same-ID retry,
remote byte admission, retention planning, strict production configuration and
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

| Path | Purpose |
| --- | --- |
| `/var/lib/enterprise-doc-backup/releases/<package-id>/` | Verified code and Linux age binary |
| `/var/lib/enterprise-doc-backup/config.json` | Public runtime configuration, root-owned and private |
| `/var/lib/enterprise-doc-backup/target.json` | Separately provisioned scoped target credentials, root-owned mode 0600 |
| `/var/lib/enterprise-doc-backup/state/` | Durable attempt state and bounded encrypted upload spool |
| `/etc/systemd/system/enterprise-doc-backup.service` | Owned service unit |

The source adapter reads only the selected database/object-store environment fields
from the existing API deployment. The daemon consumes that JSON in memory. The
target adapter requires exactly endpoint, bucket, access, secret and region fields;
it rejects another file path or additional admin credentials. Protected JSON reads
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

Remaining work is the actual target/bucket credential package, trusted
restore/protection catalog, approved retention execution, the specific remote
sensitive-backup storage exception, production activation/monitor configuration,
and continuous operation while the personal computer is offline. This local
installation check does not replace those requirements or the other acceptance gates.
