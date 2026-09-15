"""Conservative server-wide limits, shared by web, RQ and local development."""

import os


def setting(name, default, minimum=1, maximum=10000):
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def job_timeout(agent_name):
    if agent_name == "Pipeline Director":
        return setting("DENZO_DIRECTOR_TIMEOUT", 7200, 60, 14400)
    return setting("DENZO_AGENT_TIMEOUT", 1200, 60, 7200)


def queue_ttl():
    return setting("DENZO_QUEUE_TTL", 1800, 60, 86400)


def running_limit(agent_name):
    if agent_name == "Pipeline Director":
        return setting("DENZO_MAX_RUNNING_DIRECTORS", 1, 1, 4)
    return setting("DENZO_MAX_RUNNING_AGENTS", 1, 1, 8)


class CapacityExceeded(Exception):
    """Admission was refused without starting another process."""


class RuntimeLimitExceeded(Exception):
    """The job must stop and must not be automatically restarted."""


def check_cancelled():
    from denzo.agents.base_agent import _sqlite_local
    from denzo.execution import AgentCancelled

    token = getattr(_sqlite_local, "job_token", None)
    if token and token.is_set():
        if token.reason:
            raise RuntimeLimitExceeded(token.reason)
        raise AgentCancelled()


def interruptible_wait(seconds, stop=None):
    import time
    from denzo.execution import AgentCancelled

    end = time.monotonic() + max(0, seconds)
    while True:
        check_cancelled()
        if stop and stop.is_set():
            raise AgentCancelled()
        remaining = end - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.25, remaining))
