class BryceError(RuntimeError):
    """Base class for errors that are safe to show to the agent."""


class BryceNotFound(BryceError):
    pass


class FocusLost(BryceError):
    """Something other than Bryce would have received the input."""


class UserInterrupted(BryceError):
    """The human moved the mouse while a sequence was running."""


class UnexpectedScreen(BryceError):
    """Bryce is not showing what the next step depends on."""


class ConfirmationRequired(BryceError):
    pass
