class BrowserSessionError(Exception):
    code = "browser_session_error"

    def __init__(self) -> None:
        super().__init__(self.code)


class BrowserSessionInvalid(BrowserSessionError):
    code = "browser_session_invalid"


class BrowserContextStale(BrowserSessionError):
    code = "browser_context_stale"


class BrowserPrincipalForbidden(BrowserSessionError):
    code = "browser_principal_forbidden"


class BrowserIdentityConflict(BrowserSessionError):
    code = "browser_identity_conflict"


class BrowserLoginInvalid(BrowserSessionError):
    code = "browser_login_invalid"


class BrowserLoginRateLimited(BrowserSessionError):
    code = "browser_login_rate_limited"


class BrowserSessionBusy(BrowserSessionError):
    code = "browser_session_busy"


class BrowserSessionUnavailable(BrowserSessionError):
    code = "browser_session_unavailable"
