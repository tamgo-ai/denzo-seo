"""Tenant auditor using the same evidence contract as the public auditor."""
from urllib.parse import urlsplit
from denzo.agents.base_agent import TenantAwareBaseAgent, ClientContext


class DeepTechnicalAuditor(TenantAwareBaseAgent):
    def __init__(self, ctx: ClientContext):
        super().__init__('Technical Auditor',ctx,layer=1,color='gray')

    def run(self):
        from denzo.auditor.analyzer import SiteAnalyzer
        from denzo.urls import normalize_base
        url = normalize_base(self.ctx.website_url or self.ctx.domain)
        self.set_status('working','Measuring website evidence')
        report = SiteAnalyzer(url,urlsplit(url).hostname,
                lambda _pct,msg:self.set_status('working',msg)).run_full_analysis()
        self.save_output('audit_deep',report)
        findings = report.get('findings',[])
        self.save_output('technical_audit', {
            'score':report.get('overall_score'), 'coverage':report.get('coverage',0),
            'status':report.get('status'), 'critical':[f for f in findings if f.get('severity')=='critical'],
            'high_priority':[f for f in findings if f.get('severity')=='high'],
            'quick_wins':[], 'summary':report.get('scope',report.get('error','')),
        })
        if report.get('status')=='failed':
            self.set_status('error',report.get('error','Audit unavailable'))
        else:
            self.set_status('done',f"Audit {report.get('status')}; measured coverage {report.get('coverage',0)}%")
