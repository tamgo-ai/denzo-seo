"""Run with python -m denzo.auditor.worker. One bounded process per audit."""
import logging
import multiprocessing
import time
import os
import socket
from urllib.parse import urlsplit
from denzo.auditor.queue import claim, finish, progress, heartbeat


def execute(job):
    from denzo.auditor.analyzer import SiteAnalyzer
    try:
        result = SiteAnalyzer(job['url'], urlsplit(job['url']).hostname,
                              lambda pct, step: progress(job, pct, step)).run_full_analysis()
    except Exception:
        logging.exception('Audit failed: %s', job['audit_id'])
        result = {'status': 'failed', 'commercial_ready': False, 'overall_score': None,
                  'error': 'Analysis failed; no reliable score is available.'}
    finish(job, result)


def main():
    if not os.environ.get('PAGESPEED_API_KEY'):
        raise SystemExit('PAGESPEED_API_KEY is required before processing commercial audits')
    ctx = multiprocessing.get_context('spawn')
    worker_id = f'{socket.gethostname()}:{os.getpid()}'
    while True:
        heartbeat(worker_id)
        job = claim()
        if not job:
            time.sleep(3)
            continue
        process = ctx.Process(target=execute, args=(job,))
        process.start()
        deadline = time.monotonic() + 600
        while process.is_alive() and time.monotonic() < deadline:
            heartbeat(worker_id, job['audit_id'])
            process.join(min(30, max(0, deadline-time.monotonic())))
        if process.is_alive():
            process.terminate()
            process.join(10)
            if process.is_alive():
                process.kill()
                process.join()
            finish(job, {'status': 'failed', 'overall_score': None,
                         'error': 'Analysis exceeded the time limit; retry required.'})
        elif process.exitcode:
            # Lease expiry makes a crashed process eligible for bounded recovery.
            logging.error('Audit process exited with code %s', process.exitcode)


if __name__ == '__main__':
    main()
