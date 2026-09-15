"""RQ job entry point. Exceptions propagate to RQ's failed-job registry."""
from denzo.execution import execute_job


def run_agent_job(job_id):
    return execute_job(job_id)
