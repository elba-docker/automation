"""
Synchronization methods/constructs used to handle high-level control flow in
the automation script (mainly, communicating termination to all threads).
"""

import threading

stopping = False # pylint: disable=invalid-name
term_cond = threading.Condition() # pylint: disable=invalid-name


def wait(delay):
    term_cond.acquire()
    term_cond.wait(timeout=delay)
    term_cond.release()
    return stopping


def stop():
    term_cond.acquire()
    term_cond.notify_all()
    term_cond.release()
