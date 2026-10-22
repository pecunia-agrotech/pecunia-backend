"""Application-visible errors with localized user messages."""


class DomainException(Exception):
    code = "domain_error"


class RecordNotFoundException(DomainException):
    code = "not_found"


class VersionConflictException(DomainException):
    code = "version_conflict"


class InvalidMetricsException(DomainException):
    code = "invalid_metrics"


class ConfirmationRequiredException(DomainException):
    code = "confirmation_required"
