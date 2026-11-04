"""Keep recovery URLs out of Django access and error log messages."""
from copy import copy
import logging
import re


def _encoded_literal(value):
    # The access log may contain a raw percent-encoded URL while Django resolves
    # its decoded path. Match both without decoding attacker-supplied newlines.
    return ''.join(f'(?:{re.escape(char)}|%{ord(char):02x})' for char in value)


RECOVERY_PATH = re.compile(
    '(' + _encoded_literal('/accounts/nouveau-mot-de-passe/') + ')'
    r'[^/\s?\#"\']+(?:/|%2f)[^/\s?\#"\']+',
    re.IGNORECASE,
)


def _redact(value):
    if isinstance(value, str):
        return RECOVERY_PATH.sub(r'\1[masque]/[masque]', value)
    if isinstance(value, tuple):
        return tuple(_redact(item) for item in value)
    if isinstance(value, dict):
        return {key: _redact(item) for key, item in value.items()}
    return value


class PasswordResetLogFilter(logging.Filter):
    def filter(self, record):
        record.msg = _redact(record.msg)
        record.args = _redact(record.args)
        if record.stack_info:
            record.stack_info = _redact(record.stack_info)
        if record.exc_text:
            record.exc_text = _redact(record.exc_text)
        elif record.exc_info:
            formatted = logging.Formatter().formatException(record.exc_info)
            redacted = _redact(formatted)
            if redacted != formatted:
                record.exc_text = redacted
                # Custom handlers must not recreate a secret-bearing exception
                # after this filter. The readable, redacted traceback remains.
                record.exc_info = None
        request = getattr(record, 'request', None)
        if request is not None and RECOVERY_PATH.search(getattr(request, 'path', '')):
            # Error reporters receive a safe copy. Never alter the live request
            # while its recovery form still needs to validate the original URL.
            safe = copy(request)
            safe.path = _redact(request.path)
            safe.path_info = _redact(request.path_info)
            safe.META = _redact(request.META)
            match = getattr(request, 'resolver_match', None)
            if match is not None:
                safe.resolver_match = copy(match)
                safe.resolver_match.kwargs = {key: '[masque]' for key in match.kwargs}
            record.request = safe
        return True


_FILTER = PasswordResetLogFilter()


def install_password_reset_log_filter():
    # Preserve Django's configured handlers and their existing security filters.
    # Handler filters cover child loggers; logger filters also cover assertLogs
    # and handlers attached later by the normal development server.
    for name in ('django', 'django.server', 'django.request', 'django.security',
                 'django.security.csrf', 'django.security.DisallowedHost'):
        logger = logging.getLogger(name)
        logger.addFilter(_FILTER)
        for handler in logger.handlers:
            handler.addFilter(_FILTER)
