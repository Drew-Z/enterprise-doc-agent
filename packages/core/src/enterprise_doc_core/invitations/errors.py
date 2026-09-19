class InvitationError(Exception):
    code = "invitation_error"


class InvitationUnavailable(InvitationError):
    code = "invitations_unavailable"


class InvitationForbidden(InvitationError):
    code = "invitation_forbidden"


class InvitationNotFound(InvitationError):
    code = "invitation_not_found"


class InvitationDenied(InvitationError):
    code = "invitation_denied"


class InvitationConflict(InvitationError):
    code = "invitation_conflict"


class InvitationAccountConflict(InvitationError):
    code = "invitation_account_conflict"


class InvitationIneligible(InvitationError):
    code = "invitation_entitlement_required"


class InvitationLimitReached(InvitationError):
    code = "invitation_limit_reached"


class InvitationBusy(InvitationError):
    code = "invitation_busy"
