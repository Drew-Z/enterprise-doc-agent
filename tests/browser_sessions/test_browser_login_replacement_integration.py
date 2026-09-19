from __future__ import annotations

import pytest

from enterprise_doc_core.browser_sessions.errors import BrowserLoginInvalid, BrowserSessionInvalid
from enterprise_doc_core.browser_sessions.service import BrowserSessionService

from .conftest import BrowserDatabase
from .support import IDENTITY, ISSUER, RATE_KEY

pytestmark = pytest.mark.integration


async def test_new_login_supersedes_exchange_already_in_flight(browser_db: BrowserDatabase) -> None:
    service = BrowserSessionService(session_factory=browser_db.sessions, trusted_issuer=ISSUER)
    older = await service.begin_login(rate_key=RATE_KEY)
    claim = await service.claim_login(state=older.state, verifier=older.verifier)
    newer = await service.begin_login(rate_key=RATE_KEY, previous_login_verifier=older.verifier)
    with pytest.raises(BrowserLoginInvalid):
        await service.complete_login(attempt_id=claim.attempt_id, identity=IDENTITY)
    latest = await service.claim_login(state=newer.state, verifier=newer.verifier)
    issued = await service.complete_login(attempt_id=latest.attempt_id, identity=IDENTITY)
    assert (await service.get_session(credential=issued.credential)).selected is None


async def test_unreceived_login_cookie_cannot_later_restore_superseded_identity(
    browser_db: BrowserDatabase,
) -> None:
    service = BrowserSessionService(session_factory=browser_db.sessions, trusted_issuer=ISSUER)
    older = await service.begin_login(rate_key=RATE_KEY)
    claim = await service.claim_login(state=older.state, verifier=older.verifier)
    issued = await service.complete_login(attempt_id=claim.attempt_id, identity=IDENTITY)
    # The older HTTP response has not reached the browser yet, so its session cookie is absent.
    newer = await service.begin_login(rate_key=RATE_KEY, previous_login_verifier=older.verifier)
    with pytest.raises(BrowserSessionInvalid):
        await service.get_session(credential=issued.credential)
    claim = await service.claim_login(state=newer.state, verifier=newer.verifier)
    await service.complete_login(attempt_id=claim.attempt_id, identity=IDENTITY)


async def test_reauthentication_keeps_present_session_until_verified(
    browser_db: BrowserDatabase,
) -> None:
    service = BrowserSessionService(session_factory=browser_db.sessions, trusted_issuer=ISSUER)
    older = await service.begin_login(rate_key=RATE_KEY)
    claim = await service.claim_login(state=older.state, verifier=older.verifier)
    issued = await service.complete_login(attempt_id=claim.attempt_id, identity=IDENTITY)
    newer = await service.begin_login(
        rate_key=RATE_KEY,
        previous_login_verifier=older.verifier,
        previous_credential=issued.credential,
    )
    assert await service.get_session(credential=issued.credential)
    claim = await service.claim_login(
        state=newer.state, verifier=newer.verifier, previous_credential=issued.credential
    )
    rotated = await service.complete_login(
        attempt_id=claim.attempt_id, identity=IDENTITY, previous_credential=issued.credential
    )
    assert rotated.snapshot.session_id == issued.snapshot.session_id
    with pytest.raises(BrowserSessionInvalid):
        await service.get_session(credential=issued.credential)
