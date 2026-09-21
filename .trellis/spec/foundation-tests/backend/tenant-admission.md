# Tenant Admission Verification

## Scope / Trigger

Use for admission state, account reuse, atomic provisioning, credential delivery
and additive PostgreSQL migration changes. Local domain tests do not validate a
real OIDC provider, a paying customer or commercial seat enforcement.

## Signatures

`tests/admission/conftest.py::admission_db` supplies a real AsyncEngine and session
factory in a unique `admission_test_<uuid>` schema. Only identity/audit dependencies
are copied, then the actual 0023 migration runs through Alembic Operations.

## Contracts

- Assert loopback/local environment and current_schema before setup. Create the
  dependency copies with `checkfirst=False`: checkfirst can see public tables
  through search_path and silently skip local copies. Assert every dependency
  table is present in the new schema before running a migration.
- MigrationContext receives target_metadata for real naming conventions.
  Autogenerate comparisons filter the expected table names; public-only tables
  can remain visible through search_path even when current_schema is isolated.
- Every fixture drops only its generated schema in finally and verifies its
  absence. This removes test users as well as tenants; deleting Tenant alone
  does not clean globally stored User rows.
- Concurrent accept uses multiple real connections. A queued row lock can wait
  through another waiter: follow pg_blocking_pids recursively when counting all
  contenders. Prove the waiter began before expiry and release after database
  wall time reaches expiry; do not substitute a mocked clock for this behavior.
- Private file tests remove their own exact paths in finally. Windows tests
  inspect the real protected DACL and OWNER RIGHTS SID, not just POSIX mode bits.
- Lost acknowledgement testing first commits through the real issue method,
  then injects a transport-like failure; show must resolve the persisted grant.
- Formal operator tests use the actual packaged module subprocess and process-only
  settings against the fixture's isolated local database. A staging/production
  configuration under test does not mean a live environment was contacted.
- Period integration adds the real 0026 migration to the owned admission schema;
  domain acceptance opens the company, then the operator configures the period.
  Verify consumed quota survives identical replay and only one configuration audit
  exists. Keep real services; inject lost acknowledgement at psycopg's commit boundary.

## Validation & Error Matrix

| Check | Required observation |
|---|---|
| Audit failure after provisional account writes | Complete rollback; pending grant and only issued event |
| Duplicate accept / competing revoke | One terminal outcome, no duplicate company or audit |
| Inactive/deleted entities and changed request | Refusal; no account restoration |
| Migration down/up in isolated schema | Original identity row preserved; new schema matches ORM |
| File failure or lost commit acknowledgement | No raw secret output; exact recovery status |
| Fixture exit | Owned schema absent; no deletion by prefix/time in public |

## Good / Base / Bad Cases

Good: isolated schema contains every declared dependency before migration and all
created users disappear when it is removed. Base: unchanged legacy identity tests
receive a connection with isolated search_path and use their real services.
Bad: run cleanup against unknown public users, reset the shared database, or call
controlled identity data proof of real authentication.

## Tests Required

Run the admission contract module and all tests/admission files; integration
markers remain enabled only when infrastructure is available. Run required
backend gates and relevant existing identity/ACL tests after shared model edits.
Historical failures and resource corrections remain in task development evidence.

## Wrong vs Correct

Wrong: assume search_path alone makes create_all/checkfirst and schema reflection
safe. Correct: explicitly create and inspect the owned dependency tables, scope
reflection, run real migration operations and verify cleanup.

## Proven Examples

- `tests/admission/conftest.py`
- `tests/admission/test_admission_migration_integration.py`
- `tests/admission/test_admission_concurrency_integration.py`
- `tests/admission/test_admission_cli.py`
- `tests/admission/test_platform_operations_cli.py`
- `tests/admission/test_platform_operations_integration.py`
