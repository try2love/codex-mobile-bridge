class BridgeUnavailable(ValueError):
    """The desktop adapter is disconnected or cannot confirm an operation."""


class TaskStateUnavailable(ValueError):
    """No task evidence; distinct from known busy tasks or ambiguous processes."""


class TaskAuditIncomplete(ValueError):
    """The task audit timed out; strict lifecycle callers must still reject it."""


class ResumePreflightBlocked(BridgeUnavailable):
    """A native operation was rejected before dispatch or process startup."""
