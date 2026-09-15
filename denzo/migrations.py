"""Additive, repeatable platform migrations. Never infer ownership or approval."""


def migrate_platform(conn):
    additions = {
        'pages': {'approval_hash': 'TEXT', 'approved_at': 'TEXT', 'approved_by': 'INTEGER',
                  'deployment_status': "TEXT DEFAULT 'unknown'"},
        'keywords': {'source': "TEXT DEFAULT 'unknown'", 'measured_at': 'TEXT'},
        'competitors': {'evidence_source': "TEXT DEFAULT 'unknown'", 'observed_at': 'TEXT'},
        'geo_queries': {'citation_verified': 'INTEGER DEFAULT 0', 'citations_json': "TEXT DEFAULT '[]'", 'query_mode': "TEXT DEFAULT 'legacy_unverified'"},
        'client_context': {'target_audience': "TEXT DEFAULT ''"},
    }
    for table, fields in additions.items():
        existing = {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}
        for field, definition in fields.items():
            if field not in existing:
                conn.execute(f'ALTER TABLE {table} ADD COLUMN {field} {definition}')
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS agent_jobs (
        id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, agent_name TEXT NOT NULL,
        executor TEXT NOT NULL, status TEXT NOT NULL, cancel_requested INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP, started_at TEXT, heartbeat_at TEXT,
        completed_at TEXT, error TEXT,
        FOREIGN KEY(tenant_id) REFERENCES clients(tenant_id) ON DELETE CASCADE
    );
    CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_jobs_active
      ON agent_jobs(tenant_id, agent_name) WHERE status IN ('queued','running');
    CREATE TABLE IF NOT EXISTS publication_attempts (
        id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, page_id INTEGER NOT NULL,
        publisher TEXT NOT NULL, revision TEXT NOT NULL, status TEXT NOT NULL,
        url TEXT, error TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        completed_at TEXT,
        FOREIGN KEY(tenant_id) REFERENCES clients(tenant_id) ON DELETE CASCADE
    );
    CREATE UNIQUE INDEX IF NOT EXISTS idx_publication_active
      ON publication_attempts(tenant_id,page_id) WHERE status IN ('reserved','awaiting_deploy');
    CREATE INDEX IF NOT EXISTS idx_publication_daily ON publication_attempts(tenant_id,created_at);
    CREATE TABLE IF NOT EXISTS schedules (
        tenant_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0,
        timezone TEXT NOT NULL DEFAULT 'UTC', hour INTEGER NOT NULL DEFAULT 9,
        next_run_at TEXT, last_run_at TEXT, last_result TEXT,
        FOREIGN KEY(tenant_id) REFERENCES clients(tenant_id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS client_facts (
        id INTEGER PRIMARY KEY, tenant_id TEXT NOT NULL, statement TEXT NOT NULL,
        source TEXT NOT NULL, verified_by INTEGER NOT NULL,
        verified_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(tenant_id) REFERENCES clients(tenant_id) ON DELETE CASCADE
    );
    CREATE TRIGGER IF NOT EXISTS revoke_changed_approval
    AFTER UPDATE OF content,title,meta_title,meta_description,schema_markup,slug,type ON pages
    WHEN OLD.content IS NOT NEW.content OR OLD.title IS NOT NEW.title OR OLD.meta_title IS NOT NEW.meta_title
      OR OLD.meta_description IS NOT NEW.meta_description OR OLD.schema_markup IS NOT NEW.schema_markup
      OR OLD.slug IS NOT NEW.slug OR OLD.type IS NOT NEW.type
    BEGIN
      UPDATE pages SET approval_hash=NULL,approved_at=NULL,approved_by=NULL,quality_score=NULL,
        notes=TRIM(REPLACE(REPLACE(REPLACE(COALESCE(notes,''),'[APPROVED]',''),'[PENDING_REVIEW]',''),'[SUBMITTED]',''))
          || ' [PENDING_REVIEW]' WHERE id=NEW.id;
    END;
    CREATE TRIGGER IF NOT EXISTS archive_changed_content
    BEFORE UPDATE OF content ON pages
    WHEN OLD.content IS NOT NEW.content AND COALESCE(OLD.content,'') != ''
    BEGIN
      INSERT INTO content_versions(tenant_id,page_id,content,quality_score)
      VALUES(OLD.tenant_id,OLD.id,OLD.content,OLD.quality_score);
    END;
    """)
    migrate_limits(conn)
    # Recover orphan regeneration states; old content remains available for review.
    conn.execute("UPDATE pages SET status='draft' WHERE status IN ('pending','needs_fix')")
    conn.execute("UPDATE pages SET notes=REPLACE(notes,'[INDEXED]','[LEGACY_SUBMISSION_UNVERIFIED]') WHERE notes LIKE '%[INDEXED]%'")
    conn.commit()


def migrate_limits(conn):
    """Database gates protect inserts from every API/agent, including concurrent jobs."""
    from denzo.billing.plans import PLANS
    conn.execute('CREATE TABLE IF NOT EXISTS plan_limits(plan TEXT PRIMARY KEY,max_clients INTEGER,max_pages INTEGER,max_keywords INTEGER)')
    conn.executemany('INSERT OR REPLACE INTO plan_limits VALUES (?,?,?,?)',
                     [(key,p['max_clients'],p['max_pages'],p['max_keywords']) for key,p in PLANS.items()])
    conn.executescript('''
    CREATE VIEW IF NOT EXISTS account_limits AS
      SELECT u.id, p.max_clients,p.max_pages,p.max_keywords FROM users u
      JOIN plan_limits p ON p.plan=COALESCE(
        (SELECT s.plan FROM subscriptions s WHERE s.user_id=u.id AND s.status IN ('active','trialing') LIMIT 1),
        CASE WHEN u.plan='trial' THEN CASE WHEN datetime(u.trial_ends_at)>datetime('now') THEN 'trial' ELSE 'free' END
             WHEN u.plan IN ('starter','pro','agency') THEN u.plan ELSE 'free' END)
      WHERE u.role!='admin';
    CREATE TRIGGER IF NOT EXISTS limit_clients BEFORE INSERT ON clients
    WHEN EXISTS (SELECT 1 FROM account_limits a WHERE a.id=NEW.owner_user_id
       AND (SELECT COUNT(*) FROM clients WHERE owner_user_id=a.id)>=a.max_clients)
    BEGIN SELECT RAISE(ABORT,'Account client limit reached'); END;
    CREATE TRIGGER IF NOT EXISTS limit_pages BEFORE INSERT ON pages
    WHEN COALESCE(NEW.managed,1)=1 AND EXISTS (
      SELECT 1 FROM account_limits a JOIN clients c ON c.owner_user_id=a.id WHERE c.tenant_id=NEW.tenant_id
       AND (SELECT COUNT(*) FROM pages p JOIN clients pc ON pc.tenant_id=p.tenant_id
            WHERE pc.owner_user_id=a.id AND COALESCE(p.managed,1)=1)>=a.max_pages)
    BEGIN SELECT RAISE(ABORT,'Account generated page limit reached'); END;
    CREATE TRIGGER IF NOT EXISTS limit_keywords BEFORE INSERT ON keywords
    WHEN EXISTS (SELECT 1 FROM account_limits a JOIN clients c ON c.owner_user_id=a.id WHERE c.tenant_id=NEW.tenant_id
       AND (SELECT COUNT(*) FROM keywords k JOIN clients kc ON kc.tenant_id=k.tenant_id
            WHERE kc.owner_user_id=a.id)>=a.max_keywords)
    BEGIN SELECT RAISE(ABORT,'Account keyword limit reached'); END;
    ''')
    conn.commit()
