class ArchiveError(ValueError):
    """A stable public error code without passwords, payloads or private paths."""

    def __init__(self, code: str, details: dict | None = None):
        self.code = code
        self.details = details or {}
        super().__init__(code)
