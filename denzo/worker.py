"""RQ job entry point. Exceptions propagate to RQ's failed-job registry."""

from denzo.execution import execute_job
from rq import Worker


def run_agent_job(job_id):
    return execute_job(job_id)


def job_failed(job, connection, exc_type, exc_value, traceback):
    from denzo.execution import finish_job, cancel_children

    finish_job(job.id, "error", f"Worker failure: {exc_value}"[:300], retryable=False)
    cancel_children(job.id)


def job_stopped(job, connection):
    from denzo.execution import finish_job, cancel_children

    finish_job(job.id, "cancelled", "Stopped by user", retryable=False)
    cancel_children(job.id)


class BoundedWorker(Worker):
    def kill_horse(self, *args, **kwargs):
        from denzo.processes import descendants, kill_descendants

        if self.horse_pid:
            kill_descendants(descendants(self.horse_pid))
        return super().kill_horse(*args, **kwargs)

    def handle_work_horse_killed(self, job, retpid, ret_val, rusage):
        super().handle_work_horse_killed(job, retpid, ret_val, rusage)
        from denzo.execution import finish_job, cancel_children

        finish_job(
            job.id,
            "error",
            f"Worker terminated (exit {ret_val}); check memory and time limits",
            retryable=False,
        )
        cancel_children(job.id)
