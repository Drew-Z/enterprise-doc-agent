from datetime import datetime
from typing import Protocol

from pydantic import SecretStr

from enterprise_doc_core.admission.contracts import VerifiedAdmissionIdentity


class BrowserIdentityClient(Protocol):
    def authorization_url(
        self, *, state: SecretStr, nonce: SecretStr, verifier: SecretStr
    ) -> str: ...

    async def exchange(
        self,
        *,
        code: SecretStr,
        verifier: SecretStr,
        nonce_digest: str,
        started_at: datetime,
    ) -> VerifiedAdmissionIdentity: ...
