class UsageError(Exception):
    """Stable public error code for commercial usage accounting."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)
