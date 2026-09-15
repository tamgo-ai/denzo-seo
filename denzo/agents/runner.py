"""Agent execution using durable reservations and cooperative cancellation."""
import os
import threading
import logging
from denzo.db import get_db

_EXECUTOR_MODE = os.getenv('DENZO_EXECUTOR','thread')


class AgentRunner:
    @classmethod
    def start(cls,tenant_id,agent_name,ctx=None):
        from denzo.agents.registry import AGENT_REGISTRY,get_agent
        from denzo.context.builder import build_client_context
        from denzo.execution import reserve_job,execute_job,finish_job
        if agent_name not in AGENT_REGISTRY:
            return {'status':'error','message':'Unknown agent'}
        try:
            from denzo.billing.enforce import agent_entitled
            allowed, reason = agent_entitled(tenant_id, agent_name)
            if not allowed:
                return {'status':'prereq_failed','message':reason}
            ctx=ctx or build_client_context(tenant_id)
            agent=get_agent(agent_name,ctx)
            ready,reason=agent.check_prerequisites()
            if not ready:
                return {'status':'prereq_failed','message':reason}
            job_id=reserve_job(tenant_id,agent_name,_EXECUTOR_MODE)
            if not job_id:
                return {'status':'already_running','message':'An execution or content change is already in progress'}
            if _EXECUTOR_MODE=='rq':
                from redis import Redis
                from rq import Queue
                from denzo.worker import run_agent_job
                queue='denzo-director' if agent_name=='Pipeline Director' else 'denzo-seo'
                conn=Redis.from_url(os.getenv('REDIS_URL','redis://localhost:6379/0'))
                try:
                    Queue(queue,connection=conn).enqueue(run_agent_job,job_id,job_id=job_id,
                         job_timeout=9000 if agent_name=='Pipeline Director' else 7200,
                         result_ttl=86400,failure_ttl=604800)
                except Exception:
                    finish_job(job_id,'error','Queue unavailable; no work was started')
                    raise
            else:
                def target():
                    try:
                        execute_job(job_id,ctx)
                    except Exception:
                        logging.getLogger(__name__).exception('Agent failed: %s',agent_name)
                threading.Thread(target=target,daemon=True,name=f'agent:{tenant_id}:{agent_name}').start()
            return {'status':'started','agent':agent_name,'job_id':job_id,'executor':_EXECUTOR_MODE}
        except Exception as exc:
            return {'status':'error','message':str(exc)[:300]}

    @classmethod
    def stop(cls,tenant_id,agent_name):
        from denzo.execution import finish_job
        db=get_db()
        try:
            db.execute('BEGIN IMMEDIATE')
            rows=db.execute("SELECT * FROM agent_jobs WHERE tenant_id=? AND agent_name=? AND status IN ('queued','running')",(tenant_id,agent_name)).fetchall()
            db.execute("UPDATE agent_jobs SET cancel_requested=1 WHERE tenant_id=? AND agent_name=? AND status IN ('queued','running')",(tenant_id,agent_name));db.commit()
        finally:
            db.close()
        for row in rows:
            if row['status']=='queued':
                finish_job(row['id'],'cancelled','Cancelled before execution')
                if row['executor']=='rq':
                    try:
                        from redis import Redis
                        from rq.job import Job
                        Job.fetch(row['id'],connection=Redis.from_url(os.getenv('REDIS_URL','redis://localhost:6379/0'))).cancel()
                    except Exception:
                        logging.getLogger(__name__).warning('Queued job cancellation recorded in database: %s',row['id'])
        return {'status':'stop_requested' if rows else 'not_running','agent':agent_name}

    @classmethod
    def stop_all(cls,tenant_id):
        names=cls.running_agents(tenant_id)
        for name in names:
            cls.stop(tenant_id,name)
        db=get_db()
        try:
            db.execute("UPDATE agents SET status='idle',current_task='Reset by user',run_count=0 WHERE tenant_id=? AND NOT EXISTS (SELECT 1 FROM agent_jobs j WHERE j.tenant_id=agents.tenant_id AND j.agent_name=agents.name AND j.status IN ('queued','running'))",(tenant_id,));db.commit()
        finally:
            db.close()
        return {'status':'stop_requested','count':len(names)}

    @classmethod
    def running_agents(cls,tenant_id):
        db=get_db()
        try:
            return [r[0] for r in db.execute("SELECT agent_name FROM agent_jobs WHERE tenant_id=? AND status IN ('queued','running')",(tenant_id,))]
        finally:
            db.close()

    @classmethod
    def is_running(cls,tenant_id,agent_name):
        return agent_name in cls.running_agents(tenant_id)

    @classmethod
    def any_running(cls,tenant_id):
        return bool(cls.running_agents(tenant_id))
