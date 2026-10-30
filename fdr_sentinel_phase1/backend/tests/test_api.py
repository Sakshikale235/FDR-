import os
from pathlib import Path
from fastapi.testclient import TestClient

# Tests use an isolated SQLite file, not the developer's demo database.
test_db = Path(__file__).parent / "test_phase1.db"
os.environ["DATABASE_URL"] = f"sqlite:///{test_db.as_posix()}"
from app import database
database.DATABASE_URL = os.environ["DATABASE_URL"]
database.engine = __import__("sqlalchemy").create_engine(database.DATABASE_URL, connect_args={"check_same_thread": False})
database.SessionLocal = __import__("sqlalchemy.orm", fromlist=["sessionmaker"]).sessionmaker(bind=database.engine, autoflush=False, autocommit=False)
from app import main
main.engine = database.engine
main.SessionLocal = database.SessionLocal
main.app.dependency_overrides[main.get_db] = database.get_db
client = TestClient(main.app)

def setup_module():
    database.Base.metadata.drop_all(bind=database.engine)
    database.Base.metadata.create_all(bind=database.engine)
    with database.SessionLocal() as db:
        main.seed_demo_data(db)
    token = client.post('/api/v1/auth/login', json={'email':'admin@aicte.demo','password':'AICTE@123'}).json()['access_token']
    client.headers.update({'Authorization': f'Bearer {token}'})

def teardown_module():
    database.Base.metadata.drop_all(bind=database.engine)
    if test_db.exists(): test_db.unlink()

def test_health_and_summary():
    assert client.get('/api/v1/health').json()['status'] == 'healthy'
    data = client.get('/api/v1/summary').json()
    assert data['total_fdrs'] == 6 and data['institutions'] == 5

def test_list_search_and_detail():
    rows = client.get('/api/v1/fdrs?q=Canara').json()
    assert len(rows) >= 2
    detail = client.get(f"/api/v1/fdrs/{rows[0]['id']}")
    assert detail.status_code == 200 and 'documents' in detail.json()

def test_create_fdr_and_duplicate_guard():
    payload = {'fdr_number':'TEST-NEW-001','bank_name':'Canara Bank','holder_name':'ABC Institute of Technology','amount':123456,'issue_date':'2025-01-01','maturity_date':'2027-01-01','institution_id':1,'notes':'test record'}
    res = client.post('/api/v1/fdrs', json=payload)
    assert res.status_code == 201
    assert client.post('/api/v1/fdrs', json=payload).status_code == 409

def test_role_permissions():
    payload = {'name':'Restricted Institution','code':'RESTRICT-1','region':'West','state':'Maharashtra','contact_email':'x@example.com'}
    institution_token = client.post('/api/v1/auth/login', json={'email':'accounts@abc.demo','password':'Institute@123'}).json()['access_token']
    denied = client.post('/api/v1/institutions', json=payload, headers={'Authorization': f'Bearer {institution_token}'})
    assert denied.status_code == 403
    allowed = client.post('/api/v1/institutions', json=payload)
    assert allowed.status_code == 201

def test_invalid_status_rejected():
    res = client.patch('/api/v1/fdrs/1/status', json={'status':'MadeUp'}, headers={'x-demo-role':'scrutiny_officer'})
    assert res.status_code == 422

def test_document_upload_versioning_and_hash():
    files = {'file': ('sample.pdf', b'%PDF-1.4 fake test fixture', 'application/pdf')}
    first = client.post('/api/v1/fdrs/1/documents', files=files, headers={'x-demo-role':'admin'})
    assert first.status_code == 201 and first.json()['version'] == 1
    second = client.post('/api/v1/fdrs/1/documents', files=files, headers={'x-demo-role':'admin'})
    assert second.status_code == 201 and second.json()['version'] == 2
    assert len(first.json()['sha256']) == 64

def test_audit_events_created():
    rows = client.get('/api/v1/audit-events').json()
    assert len(rows) > 0

def test_phase2_extraction_evidence_and_no_auto_verification(tmp_path):
    import fitz
    pdf_path = tmp_path / 'sample-readable-fdr.pdf'
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 70), 'Canara Bank Fixed Deposit Receipt')
    page.insert_text((50, 100), 'FDR Number: 320045678901')
    page.insert_text((50, 130), 'Depositor: ABC Institute of Technology')
    page.insert_text((50, 160), 'Deposit Amount: INR 1,000,000')
    page.insert_text((50, 190), 'Issue Date: 12/01/2024')
    page.insert_text((50, 220), 'Maturity Date: 12/01/2026')
    doc.save(pdf_path); doc.close()
    uploaded = client.post('/api/v1/fdrs/1/documents', files={'file': ('sample-readable-fdr.pdf', pdf_path.read_bytes(), 'application/pdf')}, headers={'x-demo-role':'admin'})
    assert uploaded.status_code == 201
    before = client.get('/api/v1/fdrs/1').json()['verification_status']
    result = client.post('/api/v1/fdrs/1/extract', headers={'x-demo-role':'scrutiny_officer'})
    assert result.status_code == 200, result.text
    data = result.json()
    assert data['fields']['fdr_number']['value'] == '320045678901'
    assert data['fields']['amount']['value'] == '1000000'
    assert data['fields']['fdr_number']['evidence']['page'] == 1
    assert data['fields']['fdr_number']['evidence']['region'] is not None
    assert 'comparisons' in data and 'quality_signals' in data
    assert data['verification_status'] == before
    assert client.get('/api/v1/fdrs/1/extractions/latest').status_code == 200
    assertions = client.get('/api/v1/fdrs/1/assertions').json()
    assert any(a['source_type'] == 'OCR' and a['field_name'] == 'fdr_number' for a in assertions)

def test_phase2_human_corrections_keep_verification_separate():
    before = client.get('/api/v1/fdrs/1').json()['verification_status']
    officer_token = client.post('/api/v1/auth/login', json={'email':'officer@aicte.demo','password':'Officer@123'}).json()['access_token']
    response = client.post('/api/v1/fdrs/1/extractions/review', json={'corrections': {'holder_name': 'ABC Institute of Technology'}}, headers={'Authorization': f'Bearer {officer_token}'})
    assert response.status_code == 200, response.text
    assert response.json()['verification_status'] == before
    assert client.get('/api/v1/fdrs/1').json()['holder_name'] == 'ABC Institute of Technology'
    assert any(item['source_type'] == 'officer' for item in client.get('/api/v1/fdrs/1/assertions').json())

def test_phase2_extraction_requires_document():
    payload = {'fdr_number':'NO-DOC-001','bank_name':'Canara Bank','holder_name':'Demo Holder','amount':1000,'issue_date':'2025-01-01','maturity_date':'2027-01-01','institution_id':1}
    created = client.post('/api/v1/fdrs', json=payload, headers={'x-demo-role':'admin'})
    assert created.status_code == 201
    response = client.post(f"/api/v1/fdrs/{created.json()['id']}/extract", headers={'x-demo-role':'scrutiny_officer'})
    assert response.status_code == 409

def test_phase3_lifecycle_actions_prioritized_and_reminders_idempotent():
    actions = client.get('/api/v1/lifecycle/actions')
    assert actions.status_code == 200 and len(actions.json()) >= 1
    assert all('priority_score' in item and 'next_best_action' in item for item in actions.json())
    headers = {'x-demo-role':'admin', 'x-demo-user':'phase3.admin'}
    first = client.post('/api/v1/lifecycle/reminders/run', headers=headers)
    second = client.post('/api/v1/lifecycle/reminders/run', headers=headers)
    assert first.status_code == 200 and second.status_code == 200
    assert second.json()['created'] == 0
    assert client.get('/api/v1/lifecycle/reminders').status_code == 200

def test_phase3_replacement_chain_requires_same_institution_and_later_maturity():
    headers = {'x-demo-role':'admin', 'x-demo-user':'phase3.admin'}
    base = {'bank_name':'Canara Bank','holder_name':'ABC Institute of Technology','amount':50000,'issue_date':'2025-01-01','institution_id':1,'notes':'phase3 test'}
    old = client.post('/api/v1/fdrs', json={**base,'fdr_number':'P3-OLD','maturity_date':'2026-01-01'}, headers=headers)
    new = client.post('/api/v1/fdrs', json={**base,'fdr_number':'P3-NEW','maturity_date':'2028-01-01'}, headers=headers)
    assert old.status_code == 201 and new.status_code == 201
    linked = client.post(f"/api/v1/fdrs/{old.json()['id']}/replacement", json={'replacement_fdr_id':new.json()['id']}, headers=headers)
    assert linked.status_code == 201 and linked.json()['successor_fdr_id'] == new.json()['id']
    assert client.get(f"/api/v1/fdrs/{old.json()['id']}/lifecycle").json()['links']

def test_phase3_release_maker_checker_and_no_money_movement():
    headers_admin = {'x-demo-role':'admin', 'x-demo-user':'phase3.admin'}
    payload = {'fdr_number':'P3-RELEASE','bank_name':'Canara Bank','holder_name':'ABC Institute of Technology','amount':50000,'issue_date':'2024-01-01','maturity_date':'2025-01-01','institution_id':1,'notes':'phase3 release test'}
    created = client.post('/api/v1/fdrs', json=payload, headers=headers_admin)
    assert created.status_code == 201
    fdr_id = created.json()['id']
    client.patch(f'/api/v1/fdrs/{fdr_id}/status', json={'status':'Matured','verification_status':'Confirmed'}, headers=headers_admin)
    maker_headers = {'x-demo-role':'institution_user', 'x-demo-user':'maker.one'}
    request = client.post(f'/api/v1/fdrs/{fdr_id}/release-requests', json={'reason':'Matured deposit'}, headers=maker_headers)
    assert request.status_code == 201
    request_id = request.json()['id']
    self_approve = client.post(f'/api/v1/release-requests/{request_id}/approve', json={}, headers={'x-demo-role':'approving_authority','x-demo-user':'maker.one'})
    assert self_approve.status_code == 403
    checker_token = client.post('/api/v1/auth/login', json={'email':'authority@aicte.demo','password':'Authority@123'}).json()['access_token']
    checker = {'Authorization': f'Bearer {checker_token}'}
    assert client.post(f'/api/v1/release-requests/{request_id}/approve', json={}, headers=checker).status_code == 200
    processed = client.post(f'/api/v1/release-requests/{request_id}/process', json={}, headers=checker)
    assert processed.status_code == 200 and 'no financial transaction' in processed.json()['note']
    closed = client.post(f'/api/v1/release-requests/{request_id}/close', json={}, headers=checker)
    assert closed.status_code == 200 and closed.json()['fdr_status'] == 'Released'

def test_phase4_analytics_overview_is_consistent_with_registry():
    overview = client.get('/api/v1/analytics/overview')
    assert overview.status_code == 200
    data = overview.json()
    assert data['total_fdrs'] == client.get('/api/v1/fdrs?limit=500').json().__len__()
    assert sum(data['risk_counts'].values()) == data['total_fdrs']
    assert sum(data['maturity_buckets'].values()) == data['total_fdrs']
    assert data['institution_breakdown']
    assert 'not audited financial statements' in data['data_note']


def test_phase4_readiness_is_honest_about_demo_security_gaps():
    response = client.get('/api/v1/security/readiness')
    assert response.status_code == 200
    data = response.json()
    assert data['environment'] == 'local_demo'
    assert data['production_ready'] is False
    controls = {item['control']: item['status'] for item in data['checks']}
    assert controls['Authenticated bearer sessions'] == 'implemented_prototype'
    assert controls['Institution-level row isolation'] == 'not_implemented'


def test_phase4_audit_export_restricted_to_auditor_and_admin():
    institution_token = client.post('/api/v1/auth/login', json={'email':'accounts@abc.demo','password':'Institute@123'}).json()['access_token']
    denied = client.get('/api/v1/audit-events/export.csv', headers={'Authorization': f'Bearer {institution_token}'})
    assert denied.status_code == 403
    allowed = client.get('/api/v1/audit-events/export.csv', headers={'x-demo-role': 'auditor', 'x-demo-user': 'phase4.auditor'})
    assert allowed.status_code == 200
    assert 'text/csv' in allowed.headers['content-type']
    assert 'created_at_utc' in allowed.text


def test_institution_login_scopes_registry_and_rejects_cross_tenant_access():
    token = client.post('/api/v1/auth/login', json={'email':'accounts@abc.demo','password':'Institute@123'}).json()['access_token']
    headers = {'Authorization': f'Bearer {token}'}
    rows = client.get('/api/v1/fdrs', headers=headers).json()
    assert rows and all(row['institution_id'] == 1 for row in rows)
    other = client.get('/api/v1/fdrs/2', headers=headers)
    assert other.status_code == 404

def test_demo_role_headers_cannot_override_authenticated_identity():
    token = client.post('/api/v1/auth/login', json={'email':'accounts@abc.demo','password':'Institute@123'}).json()['access_token']
    response = client.post('/api/v1/institutions', json={'name':'Spoof Attempt','code':'SPOOF-1'}, headers={'Authorization': f'Bearer {token}', 'x-demo-role':'admin'})
    assert response.status_code == 403


def test_document_first_intake_extracts_then_confirms_record():
    import fitz
    from tempfile import NamedTemporaryFile
    doc = fitz.open(); page = doc.new_page()
    page.insert_text((50,70),'Canara Bank Fixed Deposit Receipt')
    page.insert_text((50,100),'FDR Number: INTAKE-2026-001')
    page.insert_text((50,130),'Depositor: ABC Institute of Technology')
    page.insert_text((50,160),'Deposit Amount: INR 1,250,000')
    page.insert_text((50,190),'Issue Date: 01/01/2026')
    page.insert_text((50,220),'Maturity Date: 01/01/2028')
    data=doc.tobytes(); doc.close()
    token=client.post('/api/v1/auth/login',json={'email':'accounts@abc.demo','password':'Institute@123'}).json()['access_token']
    headers={'Authorization':f'Bearer {token}'}
    uploaded=client.post('/api/v1/intake/extract',files={'file':('intake.pdf',data,'application/pdf')},headers=headers)
    assert uploaded.status_code==201, uploaded.text
    body=uploaded.json(); fields=body['extraction']['fields']
    assert fields['fdr_number']['value']=='INTAKE-2026-001'
    values={key:fields[key]['value'] for key in ['fdr_number','bank_name','holder_name','amount','issue_date','maturity_date']}
    created=client.post(f"/api/v1/intake/{body['intake_id']}/confirm",json={**values,'institution_id':1},headers=headers)
    assert created.status_code==201, created.text
    assert created.json()['fdr_number']=='INTAKE-2026-001' and created.json()['document_count']==1


def test_phase4_analytics_export_includes_all_matching_records():
    response = client.get('/api/v1/analytics/export.csv')
    assert response.status_code == 200
    assert 'FDR Number,Institution,Bank,Amount' in response.text
    assert '320045678901' in response.text


def test_phase4_reminders_refresh_when_maturity_date_changes():
    from datetime import date, timedelta
    headers = {'Authorization': client.headers['Authorization']}
    row = client.get('/api/v1/fdrs/1', headers=headers).json()
    target = date.today() + timedelta(days=30)
    changed = client.patch(f"/api/v1/fdrs/{row['id']}/maturity", json={'maturity_date':target.isoformat()}, headers=headers)
    assert changed.status_code == 200
    run = client.post('/api/v1/lifecycle/reminders/run', headers={'Authorization': client.headers['Authorization']})
    assert run.status_code == 200
    reminder_rows = client.get('/api/v1/lifecycle/reminders', headers=headers).json()
    assert any(r['fdr_id'] == row['id'] and r['milestone_days'] == 30 and r['due_date'] == (target - timedelta(days=30)).isoformat() for r in reminder_rows)


def test_institution_scope_covers_extraction_assertions_and_lifecycle_routes():
    token = client.post('/api/v1/auth/login', json={'email':'accounts@xyz.demo','password':'Institute@123'}).json()['access_token']
    headers = {'Authorization': f'Bearer {token}'}
    assert client.get('/api/v1/fdrs/1/extractions/latest', headers=headers).status_code == 404
    assert client.get('/api/v1/fdrs/1/assertions', headers=headers).status_code == 404
    assert client.get('/api/v1/fdrs/1/lifecycle', headers=headers).status_code == 404
    assert client.post('/api/v1/fdrs/1/documents', files={'file': ('x.pdf', b'%PDF-1.4 test', 'application/pdf')}, headers=headers).status_code == 404


def test_phase4_analytics_filters_are_applied_server_side():
    filtered = client.get('/api/v1/analytics/overview?bank=Canara').json()
    assert filtered['total_fdrs'] >= 2
    assert all(item['institution'] == 'ABC Institute of Technology' for item in filtered['institution_breakdown'])
    exported = client.get('/api/v1/analytics/export.csv?bank=Canara')
    assert exported.status_code == 200
    assert '320045678901' in exported.text
    assert '445612378900' not in exported.text
