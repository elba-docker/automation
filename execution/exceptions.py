"""
Possible exception types to be thrown by the automation script
"""


class ExitEarly(Exception):
    pass


class OperationFailed(Exception):
    pass
