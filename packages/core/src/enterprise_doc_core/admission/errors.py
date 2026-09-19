class AdmissionError(Exception):
    code = "admission_error"

    def __init__(self) -> None:
        super().__init__(self.code)


class AdmissionDenied(AdmissionError):
    code = "admission_denied"


class AdmissionForbidden(AdmissionError):
    code = "admission_forbidden"


class AdmissionInvalid(AdmissionError):
    code = "admission_invalid"


class AdmissionNotFound(AdmissionError):
    code = "admission_not_found"


class AdmissionConflict(AdmissionError):
    code = "admission_conflict"


class AdmissionAccountConflict(AdmissionError):
    code = "admission_account_conflict"


class AdmissionBusy(AdmissionError):
    code = "admission_busy"
