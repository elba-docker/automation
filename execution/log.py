"""
Logging utilities for indenting, formatting, and wrapping existing loggers
"""

import os
import time
import functools
import textwrap
import logzero
from logzero import logger as log
from logzero.colors import Style as ForegroundStyles


class LogFormatter(logzero.LogFormatter):
    def __init__(self, colors=True, indent=True, prefix=None):
        if colors:
            fmt = ('%(color)s%(inner)s%(end_color)s '
                   '%(prefix_color)s%(prefix)s%(end_prefix_color)s%(end_color)s%(message)s')
        else:
            fmt = '%(inner)s %(prefix)s%(message)s'
        inner = '[%(levelname)5s %(asctime)s]'
        logzero.LogFormatter.__init__(self, fmt=fmt)
        self._inner = inner
        self._indent = indent
        self._prefix = prefix

    def format_time(self, record, datefmt=None):
        created = self.converter(record.created)
        if datefmt:
            formatted = time.strftime(datefmt, created)
        else:
            date_str = time.strftime("%H:%M:%S", created)
            formatted = "%s.%03d" % (date_str, record.msecs)
        return formatted

    def format(self, record):
        try:
            message = record.getMessage()
            record.message = message
        except Exception as ex:
            record.message = "Bad message (%r): %r" % (ex, record.__dict__)

        record.asctime = self.format_time(record)

        if record.levelno in self._colors:
            record.color = self._colors[record.levelno]
            record.end_color = self._normal
        else:
            record.color = record.end_color = ''

        if not hasattr(record, "prefix"):
            record.prefix = self._prefix or ""

        if not hasattr(record, "prefix_color"):
            record.prefix_color = ForegroundStyles.DIM

        if not hasattr(record, "end_prefix_color"):
            record.end_prefix_color = ForegroundStyles.RESET_ALL

        record.levelname = custom_levelname(record.levelname)
        record.inner = self._inner % record.__dict__
        # Remove all blank-only lines
        lines = [line for line in record.message.splitlines()
                 if len(line.strip()) > 0]

        if self._indent:
            inner_indent = len(record.inner) + len(record.prefix) + 1
            indent = " " * inner_indent
            new_lines = []
            effective_width = int(COLUMNS) - inner_indent
            for i, line in enumerate(lines):
                wrapped = None
                if len(line) > effective_width:
                    wrapped = textwrap.wrap(line, effective_width)
                else:
                    wrapped = [line]
                for j, wrapped_line in enumerate(wrapped):
                    if i != 0 or j != 0:
                        new_lines.append(indent + wrapped_line)
                    else:
                        new_lines.append(wrapped_line)
            lines = new_lines

        record.message = "\n".join(lines)
        formatted = self._fmt % record.__dict__

        if record.exc_info:
            if not record.exc_text:
                record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            lines = [formatted.rstrip()]
            lines.extend(
                str(ln) for ln in record.exc_text.split('\n'))
            formatted = '\n'.join(lines)
        return formatted


def custom_levelname(name):
    if name == "DEBUG" or name == "ERROR":
        return name
    else:
        return name[:4]


def setup_logger(colors=True, inner=None, indent=True, prefix=None, **kwargs):
    new_logger = logzero.setup_logger(formatter=LogFormatter(
        colors=colors, indent=indent, prefix=prefix), **kwargs)
    if inner is not None:
        setattr(new_logger, "inner", inner)
    return new_logger


def _log(logger, level, message, *args, external=False, internal=False, **kwargs):
    picked_logger = None
    if internal:
        if logger != log:
            picked_logger = logger
    elif external:
        picked_logger = log
        try:
            # attempt to extract inner logger if valid
            inner = logger.inner
            if inner is not None:
                picked_logger = inner
        except:
            pass
    else:
        picked_logger = logger

    if picked_logger is not None:
        method = getattr(picked_logger, level)
        if method is not None:
            method(message, *args, **kwargs)


def info(logger, message, *args, external=False, internal=False, **kwargs):
    _log(logger, "info", message, *args, external=external, internal=internal, **kwargs)


def error(logger, message, *args, external=False, internal=False, **kwargs):
    _log(logger, "error", message, *args, external=external, internal=internal, **kwargs)


def debug(logger, message, *args, external=False, internal=False, **kwargs):
    _log(logger, "debug", message, *args, external=external, internal=internal, **kwargs)


def warning(logger, message, *args, external=False, internal=False, **kwargs):
    _log(logger, "warning", message, *args, external=external, internal=internal, **kwargs)


def fatal(logger, message, *args, external=False, internal=False, **kwargs):
    _log(logger, "fatal", message, *args, external=external, internal=internal, **kwargs)


def with_logger(cls):
    # pylint: disable=protected-access, redefined-outer-name

    old_init = cls.__init__
    @functools.wraps(old_init)
    def __init__(self, *args, logger=log, **kwargs):
        self.logger = logger
        old_init(self, *args, **kwargs)

    def _info(self, message, *args, external=False, internal=False, **kwargs):
        info(self.logger, message, *args, external=external, internal=internal, **kwargs)

    def _error(self, message, *args, external=False, internal=False, **kwargs):
        error(self.logger, message, *args, external=external, internal=internal, **kwargs)

    def _debug(self, message, *args, external=False, internal=False, **kwargs):
        debug(self.logger, message, *args, external=external, internal=internal, **kwargs)

    def _warning(self, message, *args, external=False, internal=False, **kwargs):
        warning(self.logger, message, *args, external=external, internal=internal, **kwargs)

    def _fatal(self, message, *args, external=False, internal=False, **kwargs):
        fatal(self.logger, message, *args, external=external, internal=internal, **kwargs)

    def set_logger(self, logger):
        self.logger = logger

    setattr(cls, 'info', _info)
    setattr(cls, 'error', _error)
    setattr(cls, 'debug', _debug)
    setattr(cls, 'warning', _warning)
    setattr(cls, 'fatal', _fatal)
    setattr(cls, 'set_logger', set_logger)
    setattr(cls, '__init__', __init__)
    return cls


_, COLUMNS = os.popen('stty size', 'r').read().split()
logzero.formatter(LogFormatter())
log = log  # pylint: disable=invalid-name, self-assigning-variable
