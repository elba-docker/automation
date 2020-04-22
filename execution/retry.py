"""
Constructs for implementing resilient retry loops for potentially fallible
actions
"""

import traceback
from execution.control import wait
from execution.log import with_logger
from execution.exceptions import OperationFailed, ExitEarly


class BackoffPolicy():
    # pylint: disable=no-self-argument, invalid-name
    def CONSTANT(index, backoff_duration):
        return backoff_duration

    def LINEAR(factor=0.5):
        def policy(index, backoff_duration):
            return backoff_duration * (1 + (factor * index))
        return policy

    def QUADRATIC(factor=0.5):
        def policy(index, backoff_duration):
            return backoff_duration * (1 + (factor * (index ** 2)))
        return policy


@with_logger
class retry():  # pylint: disable=invalid-name
    def __init__(self, retry_count=5, task="task", backoff_duration=60,
                 backoff_policy=BackoffPolicy.QUADRATIC(factor=0.5)):
        self._retry_count = retry_count
        self._backoff_policy = backoff_policy
        self._backoff_duration = backoff_duration
        self._index = -1
        self._task = task
        self._failure_msg = None
        self._cause = None
        self._trace = None

    def __iter__(self):
        return self

    def attempt_str(self):
        i = self._index
        if i % 10 == 1:
            return f"{i}st attempt"
        elif i % 10 == 2:
            return f"{i}nd attempt"
        elif i % 10 == 3:
            return f"{i}rd attempt"
        else:
            return f"{i}th attempt"

    def failed(self, message=None, cause=None):
        # When only one parameter is supplied, see if it is an exception
        if message is not None and cause is None and isinstance(message, Exception):
            self._cause = message
        else:
            self._failure_msg = message
            self._cause = cause

        if self._cause is not None and isinstance(self._cause, Exception):
            try:
                self._trace = traceback.format_exc()
            except:
                pass

    def is_last(self):
        return self._index >= self._retry_count + 1

    def index(self):
        return self._index

    def __next__(self):
        self._index += 1

        # first, check if the iteration is too far
        if self._index >= self._retry_count:
            # Handle exiting with too many retries and log error
            count = self._retry_count
            attempt_text = "after {:d} {}".format(
                count, "attempt" if count == 1 else "attempts")
            if isinstance(self._task, tuple):
                internal_msg, external_msg = self._task
                self.error("Failed to %s %s", internal_msg, attempt_text, internal=True)
                self.error("Failed to %s %s", external_msg, attempt_text, external=True)
            else:
                self.error("Failed to %s %s", self._task, attempt_text)
            raise OperationFailed(self._task, attempt_text)

        # otherwise, if this is not the first iteration, report a failure
        if self._index != 0:
            retry_delay = self._backoff_policy(
                self._index, self._backoff_duration)
            retry_text = "retrying in {:.1f} {}".format(
                retry_delay, "seconds" if retry_delay != 1 else "second")
            attempt_text = f" after the {self.attempt_str()}"
            failure_text = (f" [{self._failure_msg}]{attempt_text}"
                            if self._failure_msg is not None
                            else attempt_text)
            if isinstance(self._task, tuple):
                internal_msg, external_msg = self._task
                self.warning("Failed to %s%s; %s", internal_msg, failure_text, retry_text,
                             internal=True)
                self.warning("Failed to %s%s; %s", external_msg, failure_text, retry_text,
                             external=True)
            else:
                self.warning("Failed to %s%s; %s", self._task, failure_text, retry_text)
            # Print cause exception if valid
            if self._cause is not None:
                trace_msg = ""
                if self._trace is not None:
                    trace_msg = f"\n{self._trace}"
                self.debug("Caused by:\n%s%s", repr(self._cause), trace_msg)
            self._failure_msg = None
            self._cause = None
            self._trace = None

            # Sleep for the backoff duration
            if wait(retry_delay):
                raise ExitEarly()

        return self


class RetriesExceeded(Exception):
    pass
