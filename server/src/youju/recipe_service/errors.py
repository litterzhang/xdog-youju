"""Redacted control-plane errors; safe for authenticated internal API responses."""


class ServiceError(ValueError):
    pass


class ServiceBusy(ServiceError):
    pass
