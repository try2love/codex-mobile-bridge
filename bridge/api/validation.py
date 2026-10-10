"""Field locations for private desktop validation responses."""
class FieldError(ValueError):
    def __init__(self, message, field, connection_id=None):
        super().__init__(message)
        self.validation = {'field': field, 'connectionId': connection_id}


def at_field(field, callback, *args, **kwargs):
    try:
        return callback(*args, **kwargs)
    except ValueError as exc:
        raise FieldError(str(exc), field) from exc
