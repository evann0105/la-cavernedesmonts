class PauseError(ValueError):
    pass


class PauseConflict(PauseError):
    pass


class PurchaseGateBusy(PauseError):
    pass


class PurchasesPaused(PauseError):
    def __init__(self, message, *, state=None):
        super().__init__(message)
        self.state = state or {}
