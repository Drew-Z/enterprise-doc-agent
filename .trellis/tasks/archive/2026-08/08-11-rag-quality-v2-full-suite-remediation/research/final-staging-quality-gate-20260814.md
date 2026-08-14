# Final Staging Quality Gate 2026-08-14

## Evidence Boundary

This record contains only immutable deployment identity, sealed report status, stable case IDs,
allowlisted diagnostic codes, aggregate metrics, behavior versions, and SHA-256 digests. It excludes
answer text, citation excerpts, runtime identifiers, credentials, tokens, object keys, and provider
payloads. Existing reports and pre-task evidence were not modified.

## Immutable Deployment

- Commit: `9131632f54dfdad42562f1d847036a14d47eb19c`.
- GitHub Deploy Staging run `31707213320`, attempt 2: passed migration, workload rollout,
  embedding/reindex, readiness smoke, authenticated upload smoke, and sanitized evidence checks.
- GitHub Self-Hosted Quality run `31706594863`: passed for the same commit.
- API image: `sha256:797f56432c1e1db92d980851257f6e749d0bd3e3d3152477f2c4e27af9ad1112`.
- Worker image: `sha256:cfddae98e0d6ed100d58517e7f276d75b375d83806e80ec1a2bc88169dfad485`.
- Consumer image: `sha256:1138894d06c70a1c3f0f883b920079382a0ada47803eb8723dde194db0b425dd`.
- Web image: `sha256:ba85f994912b4743a4b393374a2bf666a38a43317409f91ba0b60bda7d841046`.
- Release artifact `9187655716`, named
  `staging-release-9131632f54dfdad42562f1d847036a14d47eb19c`; downloaded artifact SHA-256
  `52d9cdda15a1a72d69a2b03744ef62731d76a6682135c9f0d91b1b9e729cc527`.

## Fixed Evaluation Identity

- Dataset: `enterprise-rag-quality-v2`, SHA-256
  `145df783dba7ee1c533a59de288bb1a9aff6ba3c0bdff7e4617a366dca1a9f5b`.
- Evaluator: `m5.rag-quality.v4`.
- Behavior versions: graph `m4.v2`, prompt `m4.v9`, tool schema `m4.v2`.
- Every accepted full report records the same commit, dataset SHA, evaluator version, behavior
  versions, and sanitized command shape. Every payload seal was independently recomputed.

## Targeted And Bounded Gates

The seven-case targeted set passed three consecutive complete runs:

| Run | Status | File SHA-256 | Payload SHA-256 |
| --- | --- | --- | --- |
| `targeted-20260813-34` | passed | `622028087e596ee0c73162aff82cc7940d3abc289f3ae3d15782e23d95792486` | `b51f70fb952a186426f0155a9520eac9e4b710ab1c93b29c9af2daa69a9b2a06` |
| `targeted-20260813-35` | passed | `df05d0521a135aa568731d361b3683c820b39c7034f186b994fc2b8d7069262e` | `d891b54f24227406bf6960171760a6a500fecd33d934889f7c20cce3a2ad2985` |
| `targeted-20260813-36` | passed | `ad64523606642953f4e37d0bcb729211b729eb23eccb9f0fd911982d6125c508` | `dca58593bec9468fd6b4d79a3f248ca7156cfae23e052c84c8424896734a6e82` |

The bounded sample then passed with `coverage=bounded_sample`, `selected_case_count=12`, all
applicable targets satisfied, and no failed case:

- Run: `bounded-20260814-1`.
- File SHA-256: `a7dcde38b17469af25a2d3ee5f7bae9413f42ccd7f036216844db09581fd98bc`.
- Payload SHA-256: `9bbd8311ed20854b0e193fec0807c7d8a48157b0d579035060f1b6fee541930a`.

## Complete 40-Case Gate

All full runs used `coverage=full`, selected all 40 of 40 cases, and applied all eight unchanged
aggregate targets. Run `-2` reset the consecutive-pass count; runs `-3`, `-4`, and `-5` then formed
the required three-run sequence.

| Run | Status | File SHA-256 | Payload SHA-256 | Safe diagnostic summary |
| --- | --- | --- | --- | --- |
| `complete-20260814-1` | passed | `52d580503be3213478fa65855b3344e4b3be5902a97e041a98ffb45f31319022` | `8ca60d52b90d85b8cd41839075fa134dd9aa553b688db81cd7442b5640c344eb` | aggregate gate passed |
| `complete-20260814-2` | failed | `b87db6d6a3a289dd0e92530749b6effa0835a9f21bf4e03e3bdaa1cbd95f97f6` | `1d0977474470b5ae44b7480916deead20d61729ed5290be4baeb94ce9a4d458c` | `empty_evidence=2`; `insufficient_evidence=4`; consecutive count reset |
| `complete-20260814-3` | passed | `72ecde84abcf5085b1b1c9643a498f7582fca3960c4ab1749ccc5bc2958497ed` | `4fa356268e6f5893ffcb5b2c14a93758ff59a5194432def6147860b53ba4811b` | case diagnostics: `fact-security-encryption-rest`, `hard-sla-994-credit` |
| `complete-20260814-4` | passed | `31790aec0142e3df92904477179b0fc8982a8893404d8250e415002c1ee9ed3b` | `6135a6b559bf378ed4edd83b81a6dbdc1d437af6a125a6ae0f43a7ee9379d961` | case diagnostic: `hard-sla-994-credit` |
| `complete-20260814-5` | passed | `fb012c4807fd61ba90ff412a66f06849c221e6eace2e20bf1fa4365beb047d1f` | `825351af35215d369dc41b6fde24e86d9d1341e6c44ddcdb39e345dfa24e9cd9` | case diagnostics: `hard-sla-994-credit`, `safety-travel-first-class` |

Case-level diagnostics do not override the reviewed gate contract: the complete-suite requirement
is that all eight unchanged aggregate targets pass. The accepted consecutive sequence met that
contract in every run.

## Final Health Check

- No evaluator Job remained active after `complete-20260814-5`.
- API `2/2`, Worker `1/1`, Consumer `1/1`, and Web `2/2` replicas were Ready, updated, and
  available.
- All six corresponding application Pods were Running and Ready with zero container restarts.
- Temporary evaluation resources and immutable evidence remain retained for audit; no pre-task
  report was overwritten.

## Result

The real-staging quality gate is converged for commit `9131632f54dfdad42562f1d847036a14d47eb19c`
on the fixed deployment and evaluation identity above: targeted three-run pass, bounded 12-case
pass, and full 40-case three-run consecutive pass, with every accepted payload seal independently
verified.
