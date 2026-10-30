import hashlib, os, shutil, json, csv, io, hmac, base64, secrets, uuid, threading, time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Header, Query
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, or_
from .database import Base, engine, get_db, SessionLocal
from .models import Institution, FDRRecord, FDRDocument, AuditEvent, ExtractionRun, FieldAssertion, LifecycleLink, LifecycleReminder, ReleaseRequest, User, IntakeUpload
from .document_intelligence import extract_document
from .schemas import InstitutionCreate, InstitutionOut, FDRCreate, FDROut, StatusUpdate

APP_DIR = Path(__file__).resolve().parents[1]
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", APP_DIR / "uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
ROLES = {"institution_user", "scrutiny_officer", "approving_authority", "auditor", "admin"}
TOKEN_SECRET = os.getenv("FDR_TOKEN_SECRET", "CHANGE_THIS_SECRET_BEFORE_DEPLOYMENT")
TOKEN_TTL_SECONDS = 8 * 60 * 60

app = FastAPI(title="FDR Sentinel X API", version="2.1.0", description="FDR Sentinel X: registry, document intelligence, scheduled lifecycle automation, secure maker-checker workflow and live analytics")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
def startup():
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        if db.query(Institution).count() == 0:
            seed_demo_data(db)
        elif db.query(User).count() == 0:
            accounts = [("admin@aicte.demo", "AICTE@123", "AICTE Administrator", "admin", None), ("officer@aicte.demo", "Officer@123", "Scrutiny Officer", "scrutiny_officer", None), ("authority@aicte.demo", "Authority@123", "Approving Authority", "approving_authority", None), ("accounts@abc.demo", "Institute@123", "ABC Accounts User", "institution_user", 1), ("accounts@xyz.demo", "Institute@123", "XYZ Accounts User", "institution_user", 2), ("auditor@aicte.demo", "Auditor@123", "AICTE Auditor", "auditor", None)]
            for email, password, name, role, institution_id in accounts:
                db.add(User(email=email, password_hash=hash_password(password), full_name=name, role=role, institution_id=institution_id))
            db.commit()
    start_reminder_scheduler()

def seed_demo_data(db: Session):
    institutions = [
        Institution(name="ABC Institute of Technology", code="AICTE-001", region="Western", state="Maharashtra", contact_email="accounts@abc.example"),
        Institution(name="XYZ College of Engineering", code="AICTE-002", region="Northern", state="Delhi", contact_email="finance@xyz.example"),
        Institution(name="LNM Technical Campus", code="AICTE-003", region="Northern", state="Rajasthan", contact_email="fdr@lnm.example"),
        Institution(name="RKP Institute", code="AICTE-004", region="Southern", state="Karnataka", contact_email="admin@rkp.example"),
        Institution(name="SRS Engineering College", code="AICTE-005", region="Eastern", state="West Bengal", contact_email="accounts@srs.example"),
    ]
    db.add_all(institutions); db.flush()
    # Small, consistent demo portfolio: six records shared by every module.
    demo_users = [
        ("admin@aicte.demo", "AICTE@123", "AICTE Administrator", "admin", None),
        ("officer@aicte.demo", "Officer@123", "Scrutiny Officer", "scrutiny_officer", None),
        ("authority@aicte.demo", "Authority@123", "Approving Authority", "approving_authority", None),
        ("accounts@abc.demo", "Institute@123", "ABC Accounts User", "institution_user", 1),
        ("accounts@xyz.demo", "Institute@123", "XYZ Accounts User", "institution_user", 2),
        ("auditor@aicte.demo", "Auditor@123", "AICTE Auditor", "auditor", None),
    ]
    for email, password, full_name, role, institution_id in demo_users:
        db.add(User(email=email, password_hash=hash_password(password), full_name=full_name, role=role, institution_id=institution_id))
    today = date.today()
    rows = [
        ("320045678901", "Canara Bank", 1000000, 90, "Expiring", "Confirmed", "High", 0),
        ("445612378900", "State Bank of India", 2550000, 690, "Active", "Confirmed", "Medium", 1),
        ("778990012233", "HDFC Bank", 1500000, 16, "Expiring", "Pending", "High", 0),
        ("665544332211", "Punjab National Bank", 800000, 77, "Active", "Confirmed", "Low", 1),
        ("998877665544", "Canara Bank", 1200000, 46, "Active", "Pending", "Medium", 0),
        ("776655443322", "ICICI Bank", 1750000, 420, "Active", "Confirmed", "Low", 1),
    ]
    for i, (num, bank, amount, days, status, verification, risk, inst) in enumerate(rows):
        maturity = today + timedelta(days=days)
        issue = maturity - timedelta(days=730)
        db.add(FDRRecord(fdr_number=num, bank_name=bank, holder_name=institutions[inst].name, amount=amount,
            issue_date=issue, maturity_date=maturity, status=status, verification_status=verification,
            risk_level=risk, institution_id=institutions[inst].id, notes="Synthetic demonstration record", created_by="seed.demo"))
    db.add(AuditEvent(actor="system", action="DEMO_DATA_SEEDED", entity_type="system", details="Synthetic Phase 1 data created"))
    db.commit()

def log_event(db: Session, actor: str, action: str, entity_type: str, entity_id: Optional[str], details: str = ""):
    db.add(AuditEvent(actor=actor, action=action, entity_type=entity_type, entity_id=entity_id, details=details))

def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 240000).hex()
    return f"pbkdf2_sha256${salt}${digest}"

def verify_password(password: str, encoded: str) -> bool:
    try:
        _, salt, expected = encoded.split("$", 2)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 240000).hex()
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False

def issue_token(user: User) -> str:
    payload = json.dumps({"sub": user.email, "exp": int(datetime.utcnow().timestamp()) + TOKEN_TTL_SECONDS}, separators=(",", ":")).encode()
    body = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    signature = hmac.new(TOKEN_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{signature}"

def current_actor(authorization: str | None = Header(default=None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Sign in to continue")
    token = authorization[7:].strip()
    try:
        body, signature = token.split(".", 1)
        expected = hmac.new(TOKEN_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected): raise ValueError("signature")
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        if int(payload["exp"]) < int(datetime.utcnow().timestamp()): raise ValueError("expired")
        with SessionLocal() as db:
            user = db.query(User).filter(User.email == payload["sub"], User.is_active == True).first()
            if not user: raise ValueError("user")
            return {"role": user.role, "user": user.email, "institution_id": user.institution_id}
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

@app.post("/api/v1/auth/login")
def login(payload: dict, db: Session = Depends(get_db)):
    email = str(payload.get("email", "")).strip().lower()
    password = str(payload.get("password", ""))
    user = db.query(User).filter(User.email == email, User.is_active == True).first()
    if not user or not verify_password(password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    log_event(db, user.email, "USER_LOGIN", "user", str(user.id), "Authenticated session created")
    db.commit()
    return {"access_token": issue_token(user), "token_type": "bearer", "expires_in": TOKEN_TTL_SECONDS, "user": {"email": user.email, "name": user.full_name, "role": user.role, "institution_id": user.institution_id}}

def ensure_write(actor, allowed):
    if actor["role"] not in allowed:
        raise HTTPException(status_code=403, detail="Your role cannot perform this action")

def scoped_fdr(db: Session, fdr_id: int, actor):
    row = db.query(FDRRecord).filter(FDRRecord.id == fdr_id).first()
    if not row or (actor["role"] == "institution_user" and row.institution_id != actor["institution_id"]):
        raise HTTPException(404, "FDR record not found")
    return row

def fdr_out(row: FDRRecord):
    return {"id": row.id, "fdr_number": row.fdr_number, "bank_name": row.bank_name, "holder_name": row.holder_name,
        "amount": row.amount, "issue_date": row.issue_date, "maturity_date": row.maturity_date, "status": row.status,
        "verification_status": row.verification_status, "risk_level": row.risk_level, "notes": row.notes or "",
        "institution_id": row.institution_id, "institution_name": row.institution.name if row.institution else "Unknown",
        "document_count": len(row.documents), "created_at": row.created_at}

@app.get("/api/v1/health")
def health(db: Session = Depends(get_db)):
    db.execute(func.count(Institution.id).select() if False else __import__('sqlalchemy').text("SELECT 1"))
    return {"status": "healthy", "service": "fdr-sentinel-x", "version": "1.0.0", "database": "connected"}

@app.get("/api/v1/summary")
def summary(db: Session = Depends(get_db), actor=Depends(current_actor)):
    today = date.today(); near = today + timedelta(days=90)
    query = db.query(FDRRecord)
    if actor["role"] == "institution_user": query = query.filter(FDRRecord.institution_id == actor["institution_id"])
    rows = query.all()
    status_counts = {s: sum(1 for r in rows if r.status.lower() == s.lower()) for s in ["Active", "Expiring", "Matured", "Renewed", "Released", "Pending Verification", "Overdue"]}
    expiring = sum(1 for r in rows if today <= r.maturity_date <= near and r.status not in {"Released", "Renewed"})
    overdue = sum(1 for r in rows if r.maturity_date < today and r.status not in {"Released", "Renewed"})
    pending = sum(1 for r in rows if r.verification_status != "Confirmed")
    total_value = sum(r.amount for r in rows if r.status not in {"Released"})
    return {"total_fdrs": len(rows), "portfolio_value": total_value, "expiring_90_days": expiring,
        "verification_pending": pending, "overdue": overdue, "institutions": (1 if actor["role"] == "institution_user" else db.query(Institution).count()),
        "status_counts": status_counts, "risk_counts": {x: sum(1 for r in rows if r.risk_level == x) for x in ["High", "Medium", "Low"]}}

# Phase 4: operational analytics and an explicit production-readiness assessment.
@app.get("/api/v1/analytics/overview")
def analytics_overview(status: str = "All", institution_id: Optional[int] = None, bank: str = "", date_from: Optional[str] = None, date_to: Optional[str] = None, db: Session = Depends(get_db), actor=Depends(current_actor)):
    today = date.today()
    query = db.query(FDRRecord).options(joinedload(FDRRecord.institution))
    if actor["role"] == "institution_user": query = query.filter(FDRRecord.institution_id == actor["institution_id"])
    if institution_id: query = query.filter(FDRRecord.institution_id == institution_id)
    if status != "All": query = query.filter(FDRRecord.status == status)
    if bank: query = query.filter(FDRRecord.bank_name.ilike(f"%{bank}%"))
    try:
        if date_from: query = query.filter(FDRRecord.maturity_date >= date.fromisoformat(date_from))
        if date_to: query = query.filter(FDRRecord.maturity_date <= date.fromisoformat(date_to))
    except ValueError: raise HTTPException(422, "Dates must use YYYY-MM-DD")
    rows = query.all()
    active_rows = [r for r in rows if r.status != "Released"]
    status_counts = {}
    risk_counts = {"High": 0, "Medium": 0, "Low": 0}
    institution_totals = {}
    maturity_buckets = {"Overdue": 0, "0–30 days": 0, "31–90 days": 0, "91–180 days": 0, "180+ days": 0}
    for row in rows:
        status_counts[row.status] = status_counts.get(row.status, 0) + 1
        risk_counts[row.risk_level] = risk_counts.get(row.risk_level, 0) + 1
        name = row.institution.name if row.institution else "Unknown"
        entry = institution_totals.setdefault(name, {"institution": name, "fdr_count": 0, "portfolio_value": 0.0, "high_risk": 0})
        entry["fdr_count"] += 1
        if row.status != "Released": entry["portfolio_value"] += float(row.amount)
        if row.risk_level == "High": entry["high_risk"] += 1
        days = (row.maturity_date - today).days
        bucket = "Overdue" if days < 0 else "0–30 days" if days <= 30 else "31–90 days" if days <= 90 else "91–180 days" if days <= 180 else "180+ days"
        maturity_buckets[bucket] += 1
    total_value = sum(float(r.amount) for r in active_rows)
    visible_ids = [r.id for r in rows]
    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "total_fdrs": len(rows),
        "active_portfolio_value": total_value,
        "average_fdr_value": total_value / max(1, len(active_rows)),
        "status_counts": status_counts,
        "risk_counts": risk_counts,
        "verification_counts": {state: sum(1 for r in rows if r.verification_status == state) for state in ["Pending", "In Review", "Confirmed", "Discrepancy"]},
        "maturity_buckets": maturity_buckets,
        "institution_breakdown": sorted(institution_totals.values(), key=lambda x: (-x["portfolio_value"], x["institution"])),
        "document_versions": db.query(FDRDocument).filter(FDRDocument.fdr_id.in_(visible_ids)).count() if visible_ids else 0,
        "open_reminders": db.query(LifecycleReminder).filter(LifecycleReminder.status == "open", LifecycleReminder.fdr_id.in_(visible_ids)).count() if visible_ids else 0,
        "open_release_requests": db.query(ReleaseRequest).filter(ReleaseRequest.status.in_(["requested", "approved", "processing"]), ReleaseRequest.fdr_id.in_(visible_ids)).count() if visible_ids else 0,
        "data_note": "Metrics are calculated from current demo records; they are operational summaries, not audited financial statements."
    }

@app.get("/api/v1/analytics/export.csv")
def export_analytics_csv(status: str = "All", institution_id: Optional[int] = None, bank: str = "", date_from: Optional[str] = None, date_to: Optional[str] = None, db: Session = Depends(get_db), actor=Depends(current_actor)):
    query = db.query(FDRRecord).options(joinedload(FDRRecord.institution))
    if actor["role"] == "institution_user": query = query.filter(FDRRecord.institution_id == actor["institution_id"])
    if institution_id: query = query.filter(FDRRecord.institution_id == institution_id)
    if status != "All": query = query.filter(FDRRecord.status == status)
    if bank: query = query.filter(FDRRecord.bank_name.ilike(f"%{bank}%"))
    try:
        if date_from: query = query.filter(FDRRecord.maturity_date >= date.fromisoformat(date_from))
        if date_to: query = query.filter(FDRRecord.maturity_date <= date.fromisoformat(date_to))
    except ValueError: raise HTTPException(422, "Dates must use YYYY-MM-DD")
    output = io.StringIO(newline=""); writer = csv.writer(output)
    writer.writerow(["FDR Number", "Institution", "Bank", "Amount", "Issue Date", "Maturity Date", "Status", "Verification", "Risk"])
    for row in query.order_by(FDRRecord.id.asc()).yield_per(500):
        writer.writerow([row.fdr_number, row.institution.name if row.institution else "", row.bank_name, row.amount, row.issue_date, row.maturity_date, row.status, row.verification_status, row.risk_level])
    output.seek(0)
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=fdr-sentinel-analytics.csv"})

@app.get("/api/v1/security/readiness")
def security_readiness():
    # HMAC bearer sessions are implemented for the local prototype; production SSO and managed secrets remain deployment tasks.
    checks = [
        {"control": "Authenticated bearer sessions", "status": "implemented_prototype", "production_required": True},
        {"control": "Production SSO / managed signing secret", "status": "deployment_required", "production_required": True},
        {"control": "Institution-level row isolation", "status": "not_implemented", "production_required": True},
        {"control": "Private encrypted object storage", "status": "not_implemented", "production_required": True},
        {"control": "Database migrations and recovery drills", "status": "not_implemented", "production_required": True},
        {"control": "Upload extension and size validation", "status": "implemented_demo", "production_required": False},
        {"control": "Document SHA-256 integrity fingerprints", "status": "implemented_demo", "production_required": False},
        {"control": "Audit event recording", "status": "implemented_demo", "production_required": True},
        {"control": "Tamper-evident append-only audit storage", "status": "not_implemented", "production_required": True},
        {"control": "Automated browser / accessibility suite", "status": "not_verified", "production_required": True},
    ]
    blockers = sum(1 for item in checks if item["production_required"] and item["status"] in {"not_implemented", "not_verified"})
    return {"environment": "local_demo", "production_ready": blockers == 0, "blocking_controls": blockers, "checks": checks, "message": "Do not use real institutional or financial data until all production controls are implemented and independently tested."}

@app.get("/api/v1/audit-events/export.csv")
def export_audit_events(db: Session = Depends(get_db), actor=Depends(current_actor)):
    ensure_write(actor, {"admin", "auditor"})
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["id", "created_at_utc", "actor", "action", "entity_type", "entity_id", "details"])
    for event in db.query(AuditEvent).order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()).limit(10000).all():
        writer.writerow([event.id, event.created_at.isoformat() if event.created_at else "", event.actor, event.action, event.entity_type, event.entity_id or "", event.details or ""])
    output.seek(0)
    log_event(db, actor["user"], "AUDIT_CSV_EXPORTED", "audit", None, "Audit event CSV export requested")
    db.commit()
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=fdr-sentinel-audit.csv"})

@app.get("/api/v1/institutions", response_model=list[InstitutionOut])
def list_institutions(q: str = "", db: Session = Depends(get_db), actor=Depends(current_actor)):
    query = db.query(Institution)
    if actor["role"] == "institution_user": query = query.filter(Institution.id == actor["institution_id"])
    if q: query = query.filter(or_(Institution.name.ilike(f"%{q}%"), Institution.code.ilike(f"%{q}%")))
    return query.order_by(Institution.name).all()

@app.post("/api/v1/institutions", response_model=InstitutionOut, status_code=201)
def create_institution(payload: InstitutionCreate, db: Session = Depends(get_db), actor=Depends(current_actor)):
    ensure_write(actor, {"admin"})
    if db.query(Institution).filter(or_(Institution.code == payload.code, Institution.name == payload.name)).first():
        raise HTTPException(409, "Institution name or code already exists")
    row = Institution(**payload.model_dump()); db.add(row); db.flush()
    log_event(db, actor["user"], "INSTITUTION_CREATED", "institution", str(row.id), row.name); db.commit(); db.refresh(row); return row

@app.get("/api/v1/fdrs")
def list_fdrs(q: str = "", status: str = "All", risk: str = "All", institution_id: Optional[int] = None,
              skip: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db), actor=Depends(current_actor)):
    query = db.query(FDRRecord)
    if actor["role"] == "institution_user": query = query.filter(FDRRecord.institution_id == actor["institution_id"]).options(joinedload(FDRRecord.institution), joinedload(FDRRecord.documents))
    if q: query = query.join(Institution).filter(or_(FDRRecord.fdr_number.ilike(f"%{q}%"), FDRRecord.bank_name.ilike(f"%{q}%"), FDRRecord.holder_name.ilike(f"%{q}%"), Institution.name.ilike(f"%{q}%")))
    if status and status != "All": query = query.filter(FDRRecord.status == status)
    if risk and risk != "All": query = query.filter(FDRRecord.risk_level == risk)
    if institution_id: query = query.filter(FDRRecord.institution_id == institution_id)
    rows = query.order_by(FDRRecord.maturity_date.asc()).offset(skip).limit(limit).all()
    return [fdr_out(row) for row in rows]

@app.get("/api/v1/fdrs/{fdr_id}")
def get_fdr(fdr_id: int, db: Session = Depends(get_db), actor=Depends(current_actor)):
    row = db.query(FDRRecord).options(joinedload(FDRRecord.institution), joinedload(FDRRecord.documents)).filter(FDRRecord.id == fdr_id).first()
    if not row or (actor["role"] == "institution_user" and row.institution_id != actor["institution_id"]): raise HTTPException(404, "FDR record not found")
    result = fdr_out(row); result["documents"] = [{"id": d.id, "filename": d.filename, "sha256": d.sha256, "size_bytes": d.size_bytes, "version": d.version, "uploaded_at": d.uploaded_at, "is_current": d.is_current} for d in sorted(row.documents, key=lambda x: x.version)]
    return result

@app.post("/api/v1/intake/extract", status_code=201)
def intake_extract(file: UploadFile = File(...), db: Session = Depends(get_db), actor=Depends(current_actor)):
    """Upload-first registration: store the source and return advisory OCR candidates before creating an FDR row."""
    ensure_write(actor, {"institution_user", "admin"})
    suffix = Path(file.filename or "document").suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS: raise HTTPException(415, "Only PDF, PNG, JPG and JPEG files are allowed")
    content = file.file.read(MAX_UPLOAD_BYTES + 1)
    if not content: raise HTTPException(400, "Uploaded file is empty")
    if len(content) > MAX_UPLOAD_BYTES: raise HTTPException(413, "File exceeds the 10 MB limit")
    intake_id = str(uuid.uuid4()); digest = hashlib.sha256(content).hexdigest()
    path = UPLOAD_DIR / f"intake_{intake_id}{suffix}"; path.write_bytes(content)
    result = extract_document(str(path))
    item = IntakeUpload(id=intake_id, filename=Path(file.filename or path.name).name, stored_path=str(path), sha256=digest, size_bytes=len(content), extracted_json=json.dumps(result), uploaded_by=actor["user"], institution_id=actor.get("institution_id"))
    db.add(item); log_event(db, actor["user"], "FDR_INTAKE_UPLOADED", "intake", intake_id, f"{item.filename}; SHA-256 {digest}"); db.commit()
    return {"intake_id": intake_id, "filename": item.filename, "sha256": digest, "size_bytes": len(content), "extraction": result, "institutions": [{"id": x.id, "name": x.name} for x in db.query(Institution).filter(Institution.id == actor["institution_id"] if actor.get("institution_id") else True).order_by(Institution.name).all()]}

@app.post("/api/v1/intake/{intake_id}/confirm", status_code=201)
def confirm_intake(intake_id: str, payload: dict, db: Session = Depends(get_db), actor=Depends(current_actor)):
    ensure_write(actor, {"institution_user", "admin"})
    intake = db.query(IntakeUpload).filter(IntakeUpload.id == intake_id, IntakeUpload.consumed == False).first()
    if not intake: raise HTTPException(404, "Intake upload not found or already submitted")
    institution_id = actor.get("institution_id") or payload.get("institution_id")
    if not institution_id or (actor.get("institution_id") and int(institution_id) != actor["institution_id"]): raise HTTPException(403, "Select an institution assigned to your account")
    required = ["fdr_number", "bank_name", "holder_name", "amount", "issue_date", "maturity_date"]
    values = {key: payload.get(key) for key in required}
    missing = [key for key, value in values.items() if value in (None, "")]
    if missing: raise HTTPException(422, {"message": "Complete all missing or incorrect fields before submitting", "missing_fields": missing})
    try:
        values["amount"] = float(values["amount"]); values["issue_date"] = date.fromisoformat(str(values["issue_date"])); values["maturity_date"] = date.fromisoformat(str(values["maturity_date"]))
    except Exception: raise HTTPException(422, "Amount and dates must be valid")
    if values["amount"] <= 0 or values["maturity_date"] < values["issue_date"]: raise HTTPException(422, "Check amount and date range")
    normalized = "".join(str(values["fdr_number"]).upper().split())
    duplicate = db.query(FDRRecord).filter(func.upper(func.replace(FDRRecord.fdr_number, " ", "")) == normalized).first()
    if duplicate: raise HTTPException(409, "An FDR with this number already exists")
    row = FDRRecord(**values, institution_id=int(institution_id), status="Pending Verification", verification_status="Pending", risk_level="Medium", notes=str(payload.get("notes", "")), created_by=actor["user"])
    db.add(row); db.flush()
    doc = FDRDocument(fdr_id=row.id, filename=intake.filename, stored_path=intake.stored_path, sha256=intake.sha256, size_bytes=intake.size_bytes, version=1, uploaded_by=actor["user"], is_current=True)
    db.add(doc); db.flush(); intake.consumed = True
    extracted = json.loads(intake.extracted_json or "{}")
    for field, candidate in (extracted.get("fields") or {}).items():
        if candidate and candidate.get("value") is not None:
            db.add(FieldAssertion(fdr_id=row.id, document_id=doc.id, field_name=field, value=str(candidate["value"]), source_type="OCR", page=(candidate.get("evidence") or {}).get("page"), region_json=json.dumps((candidate.get("evidence") or {}).get("region") or {}), confidence=candidate.get("confidence"), asserted_by=actor["user"]))
    log_event(db, actor["user"], "FDR_CREATED_FROM_DOCUMENT", "fdr", str(row.id), f"{row.fdr_number}; source {intake.filename}; human-confirmed fields"); db.commit(); db.refresh(row)
    row = db.query(FDRRecord).options(joinedload(FDRRecord.institution), joinedload(FDRRecord.documents)).get(row.id)
    return fdr_out(row)

@app.post("/api/v1/fdrs", status_code=201)
def create_fdr(payload: FDRCreate, db: Session = Depends(get_db), actor=Depends(current_actor)):
    ensure_write(actor, {"institution_user", "scrutiny_officer", "admin"})
    if payload.maturity_date < payload.issue_date: raise HTTPException(422, "Maturity date must be on or after issue date")
    institution = db.query(Institution).filter(Institution.id == payload.institution_id).first()
    if not institution: raise HTTPException(404, "Institution not found")
    if actor["role"] == "institution_user" and payload.institution_id != actor["institution_id"]: raise HTTPException(403, "You can only register FDRs for your institution")
    normalized_number = "".join(payload.fdr_number.upper().split())
    duplicate = db.query(FDRRecord).filter(func.upper(func.replace(FDRRecord.fdr_number, " ", "")) == normalized_number).first()
    if duplicate: raise HTTPException(409, "FDR number already exists")
    row = FDRRecord(**payload.model_dump(), created_by=actor["user"]); db.add(row); db.flush()
    log_event(db, actor["user"], "FDR_CREATED", "fdr", str(row.id), f"FDR {row.fdr_number} submitted"); db.commit(); db.refresh(row)
    row = db.query(FDRRecord).options(joinedload(FDRRecord.institution), joinedload(FDRRecord.documents)).get(row.id)
    return fdr_out(row)

@app.patch("/api/v1/fdrs/{fdr_id}/maturity")
def update_fdr_maturity(fdr_id: int, payload: dict, db: Session = Depends(get_db), actor=Depends(current_actor)):
    ensure_write(actor, {"institution_user", "scrutiny_officer", "admin"})
    row = db.query(FDRRecord).filter_by(id=fdr_id).first()
    if not row or (actor["role"] == "institution_user" and row.institution_id != actor["institution_id"]):
        raise HTTPException(404, "FDR record not found")
    try: new_date = date.fromisoformat(str(payload.get("maturity_date", "")))
    except ValueError: raise HTTPException(422, "maturity_date must use YYYY-MM-DD")
    previous = row.maturity_date
    row.maturity_date = new_date; row.updated_at = datetime.utcnow()
    log_event(db, actor["user"], "FDR_MATURITY_DATE_CHANGED", "fdr", str(row.id), f"{previous.isoformat()} -> {new_date.isoformat()}; reminder schedule will be reconciled automatically")
    db.commit()
    return {"fdr_id": row.id, "previous_maturity_date": previous.isoformat(), "maturity_date": new_date.isoformat(), "reminder_reconciliation": "scheduled hourly and on manual evaluation"}

@app.patch("/api/v1/fdrs/{fdr_id}/status")
def update_fdr_status(fdr_id: int, payload: StatusUpdate, db: Session = Depends(get_db), actor=Depends(current_actor)):
    ensure_write(actor, {"scrutiny_officer", "approving_authority", "admin"})
    row = db.query(FDRRecord).filter(FDRRecord.id == fdr_id).first()
    if not row: raise HTTPException(404, "FDR record not found")
    allowed_status = {"Active", "Expiring", "Matured", "Renewed", "Released", "Pending Verification", "Overdue"}
    if payload.status not in allowed_status: raise HTTPException(422, "Unsupported FDR status")
    old = row.status; row.status = payload.status
    if payload.verification_status is not None:
        if payload.verification_status not in {"Pending", "In Review", "Confirmed", "Discrepancy"}: raise HTTPException(422, "Unsupported verification status")
        row.verification_status = payload.verification_status
    if payload.risk_level is not None:
        if payload.risk_level not in {"High", "Medium", "Low"}: raise HTTPException(422, "Unsupported risk level")
        row.risk_level = payload.risk_level
    row.updated_at = datetime.utcnow(); log_event(db, actor["user"], "FDR_STATUS_UPDATED", "fdr", str(row.id), f"{old} -> {row.status}"); db.commit()
    return {"message": "FDR updated", "id": row.id, "status": row.status, "verification_status": row.verification_status, "risk_level": row.risk_level}

@app.post("/api/v1/fdrs/{fdr_id}/documents", status_code=201)
def upload_document(fdr_id: int, file: UploadFile = File(...), db: Session = Depends(get_db), actor=Depends(current_actor)):
    ensure_write(actor, {"institution_user", "scrutiny_officer", "admin"})
    row = scoped_fdr(db, fdr_id, actor)
    suffix = Path(file.filename or "document").suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS: raise HTTPException(415, "Only PDF, PNG, JPG and JPEG files are allowed")
    content = file.file.read(MAX_UPLOAD_BYTES + 1)
    if not content: raise HTTPException(400, "Uploaded file is empty")
    if len(content) > MAX_UPLOAD_BYTES: raise HTTPException(413, "File exceeds the 10 MB limit")
    digest = hashlib.sha256(content).hexdigest()
    latest = db.query(FDRDocument).filter(FDRDocument.fdr_id == fdr_id).order_by(FDRDocument.version.desc()).first()
    version = (latest.version + 1) if latest else 1
    if latest: latest.is_current = False
    safe_name = f"fdr_{fdr_id}_v{version}_{digest[:12]}{suffix}"
    destination = UPLOAD_DIR / safe_name
    destination.write_bytes(content)
    doc = FDRDocument(fdr_id=fdr_id, filename=Path(file.filename or safe_name).name, stored_path=str(destination), sha256=digest, size_bytes=len(content), version=version, uploaded_by=actor["user"], is_current=True)
    db.add(doc); log_event(db, actor["user"], "DOCUMENT_UPLOADED", "fdr_document", None, f"FDR {fdr_id}; version {version}; SHA-256 {digest}"); db.commit(); db.refresh(doc)
    return {"id": doc.id, "fdr_id": fdr_id, "filename": doc.filename, "sha256": doc.sha256, "size_bytes": doc.size_bytes, "version": doc.version, "is_current": doc.is_current}


@app.post("/api/v1/fdrs/{fdr_id}/extract")
def extract_fdr_document(fdr_id: int, db: Session = Depends(get_db), actor=Depends(current_actor)):
    """Run advisory extraction against the current uploaded document; never verifies the FDR."""
    row = scoped_fdr(db, fdr_id, actor)
    row = db.query(FDRRecord).options(joinedload(FDRRecord.documents)).filter(FDRRecord.id == fdr_id).first()
    document = next((d for d in sorted(row.documents, key=lambda x: x.version, reverse=True) if d.is_current), None)
    if not document: raise HTTPException(409, "Upload an FDR document before extraction")
    result = extract_document(document.stored_path)
    comparisons = []
    expected = {"fdr_number": row.fdr_number, "bank_name": row.bank_name, "holder_name": row.holder_name,
                "amount": str(int(row.amount)) if float(row.amount).is_integer() else str(row.amount),
                "issue_date": row.issue_date.isoformat(), "maturity_date": row.maturity_date.isoformat()}
    for field, candidate in result.get("fields", {}).items():
        value = candidate.get("value")
        if value is not None:
            actual = str(value).strip().lower()
            exp = str(expected[field]).strip().lower()
            if field == "bank_name" and actual == "sbi": actual = "state bank of india"
            if field == "bank_name" and exp == "sbi": exp = "state bank of india"
            if field == "amount":
                try: actual, exp = str(float(actual)), str(float(exp))
                except ValueError: pass
            comparisons.append({"field": field, "candidate": value, "registered_value": expected[field], "match": actual == exp,
                                "confidence": candidate.get("confidence", 0), "evidence": candidate.get("evidence")})
            db.add(FieldAssertion(fdr_id=row.id, document_id=document.id, field_name=field, value=str(value), source_type="OCR",
                                  page=(candidate.get("evidence") or {}).get("page"), region_json=json.dumps((candidate.get("evidence") or {}).get("region")),
                                  confidence=candidate.get("confidence", 0), asserted_by=actor["user"]))
    result["comparisons"] = comparisons
    result["discrepancy_count"] = sum(1 for c in comparisons if not c["match"])
    result["document"] = {"id": document.id, "filename": document.filename, "version": document.version, "sha256": document.sha256}
    run = ExtractionRun(fdr_id=row.id, document_id=document.id, status=result.get("status", "needs_manual_review"),
                        result_json=json.dumps(result), quality_json=json.dumps(result.get("quality_signals", [])), created_by=actor["user"])
    db.add(run)
    log_event(db, actor["user"], "DOCUMENT_EXTRACTION_COMPLETED", "fdr", str(row.id),
              f"Document {document.id}; run status {run.status}; {result.get('discrepancy_count', 0)} discrepancy signal(s). Verification status unchanged.")
    db.commit(); db.refresh(run)
    result["run_id"] = run.id
    result["created_at"] = run.created_at.isoformat() if run.created_at else None
    result["verification_status"] = row.verification_status
    return result

@app.get("/api/v1/fdrs/{fdr_id}/extractions/latest")
def latest_extraction(fdr_id: int, db: Session = Depends(get_db), actor=Depends(current_actor)):
    scoped_fdr(db, fdr_id, actor)
    run = db.query(ExtractionRun).filter(ExtractionRun.fdr_id == fdr_id).order_by(ExtractionRun.created_at.desc(), ExtractionRun.id.desc()).first()
    if not run: raise HTTPException(404, "No extraction run found")
    result = json.loads(run.result_json); result["run_id"] = run.id; result["created_at"] = run.created_at.isoformat(); result["verification_status"] = db.query(FDRRecord).filter(FDRRecord.id == fdr_id).first().verification_status
    return result

@app.post("/api/v1/fdrs/{fdr_id}/extractions/review")
def review_extraction(fdr_id: int, payload: dict, db: Session = Depends(get_db), actor=Depends(current_actor)):
    ensure_write(actor, {"institution_user", "scrutiny_officer", "admin"})
    row = scoped_fdr(db, fdr_id, actor)
    corrections = payload.get("corrections", {})
    allowed_fields = {"fdr_number", "bank_name", "holder_name", "amount", "issue_date", "maturity_date"}
    if not isinstance(corrections, dict) or not corrections or set(corrections) - allowed_fields:
        raise HTTPException(422, "Provide a non-empty corrections object with supported FDR fields")
    doc = db.query(FDRDocument).filter(FDRDocument.fdr_id == fdr_id, FDRDocument.is_current == True).first()
    for field, value in corrections.items():
        if value is None or not str(value).strip(): raise HTTPException(422, f"Correction for {field} cannot be empty")
        clean = str(value).strip()
        if field == "amount":
            try:
                number = float(clean.replace(",", ""))
                if number <= 0: raise ValueError()
                clean = str(number); row.amount = number
            except ValueError: raise HTTPException(422, "Amount must be a positive number")
        elif field in {"issue_date", "maturity_date"}:
            try: parsed = date.fromisoformat(clean)
            except ValueError: raise HTTPException(422, f"{field} must use YYYY-MM-DD")
            setattr(row, field, parsed); clean = parsed.isoformat()
        else: setattr(row, field, clean)
        db.add(FieldAssertion(fdr_id=row.id, document_id=doc.id if doc else None, field_name=field, value=clean, source_type="officer" if actor["role"] == "scrutiny_officer" else "institution", asserted_by=actor["user"], confidence=None))
    if row.maturity_date < row.issue_date: raise HTTPException(422, "Maturity date must be on or after issue date")
    row.updated_at = datetime.utcnow()
    log_event(db, actor["user"], "EXTRACTION_FIELDS_REVIEWED", "fdr", str(row.id), "Human-reviewed corrections: " + ", ".join(sorted(corrections)) + ". Verification status unchanged.")
    db.commit()
    return {"message": "Corrections saved with provenance", "fdr_id": row.id, "corrected_fields": sorted(corrections), "verification_status": row.verification_status, "note": "Human field correction does not authenticate or bank-verify the FDR."}

@app.get("/api/v1/fdrs/{fdr_id}/assertions")
def field_assertions(fdr_id: int, db: Session = Depends(get_db), actor=Depends(current_actor)):
    scoped_fdr(db, fdr_id, actor)
    rows = db.query(FieldAssertion).filter(FieldAssertion.fdr_id == fdr_id).order_by(FieldAssertion.created_at.desc()).limit(200).all()
    return [{"id": r.id, "field_name": r.field_name, "value": r.value, "source_type": r.source_type, "document_id": r.document_id, "page": r.page, "region": json.loads(r.region_json or "{}"), "confidence": r.confidence, "asserted_by": r.asserted_by, "created_at": r.created_at.isoformat()} for r in rows]

@app.get("/api/v1/audit-events")
def audit_events(limit: int = Query(20, ge=1, le=200), db: Session = Depends(get_db), actor=Depends(current_actor)):
    query = db.query(AuditEvent)
    if actor["role"] == "institution_user":
        ids = [str(x) for (x,) in db.query(FDRRecord.id).filter(FDRRecord.institution_id == actor["institution_id"]).all()]
        query = query.filter(or_(AuditEvent.entity_id.in_(ids), AuditEvent.actor == actor["user"]))
    rows = query.order_by(AuditEvent.created_at.desc()).limit(limit).all()
    return [{"id": r.id, "actor": r.actor, "action": r.action, "entity_type": r.entity_type, "entity_id": r.entity_id, "details": r.details, "created_at": r.created_at} for r in rows]


# Phase 3: lifecycle automation and advisory prioritisation. This is a demo scheduler:
# run POST /api/v1/lifecycle/reminders/run from a scheduler/cron in deployment.
REMINDER_MILESTONES = (90, 60, 30, 15, 7, 3, 1, 0, -7, -30)

@app.get("/api/v1/lifecycle/actions")
def lifecycle_actions(db: Session = Depends(get_db), actor=Depends(current_actor)):
    today = date.today()
    query = db.query(FDRRecord).options(joinedload(FDRRecord.institution))
    if actor["role"] == "institution_user": query = query.filter(FDRRecord.institution_id == actor["institution_id"])
    rows = query.all()
    actions = []
    for row in rows:
        if row.status in {"Released", "Renewed"}:
            continue
        days = (row.maturity_date - today).days
        if days <= 0 and row.status not in {"Matured", "Overdue"}:
            suggested, reason = "Review matured FDR", "Maturity date has passed; confirm renewal or release path."
        elif days <= 30:
            suggested, reason = "Start renewal / replacement", f"Maturity in {days} day(s); initiate the replacement workflow."
        elif row.verification_status != "Confirmed":
            suggested, reason = "Complete verification", "Bank confirmation is still pending; lifecycle actions remain advisory."
        elif row.risk_level == "High":
            suggested, reason = "Officer review", "High-risk record requires scrutiny before the next lifecycle step."
        else:
            suggested, reason = "Monitor maturity", f"Maturity in {days} day(s)."
        urgency = 100 if days < 0 else max(0, 100 - min(days, 100))
        blocker = 25 if row.verification_status != "Confirmed" else 0
        risk_points = {"High": 25, "Medium": 12, "Low": 3}.get(row.risk_level, 10)
        overdue_age = max(0, -days)
        score = min(100, urgency * 0.45 + blocker + risk_points + min(25, overdue_age))
        actions.append({"fdr_id": row.id, "fdr_number": row.fdr_number, "institution": row.institution.name if row.institution else "Unknown",
            "bank_name": row.bank_name, "amount": row.amount, "maturity_date": row.maturity_date.isoformat(), "days_to_maturity": days,
            "status": row.status, "verification_status": row.verification_status, "risk_level": row.risk_level,
            "priority_score": round(score, 1), "priority": "Critical" if score >= 75 else "High" if score >= 50 else "Normal",
            "next_best_action": suggested, "reason": reason})
    return sorted(actions, key=lambda x: (-x["priority_score"], x["days_to_maturity"], x["fdr_id"]))

@app.get("/api/v1/lifecycle/reminders")
def list_lifecycle_reminders(status: str = "All", db: Session = Depends(get_db), actor=Depends(current_actor)):
    query = db.query(LifecycleReminder).join(FDRRecord, FDRRecord.id == LifecycleReminder.fdr_id)
    if actor["role"] == "institution_user": query = query.filter(FDRRecord.institution_id == actor["institution_id"])
    if status != "All": query = query.filter(LifecycleReminder.status == status)
    rows = query.order_by(LifecycleReminder.due_date.asc(), LifecycleReminder.id.asc()).limit(500).all()
    return [{"id": r.id, "fdr_id": r.fdr_id, "milestone_days": r.milestone_days, "due_date": r.due_date.isoformat(), "status": r.status, "message": r.message, "created_at": r.created_at.isoformat()} for r in rows]

def evaluate_lifecycle_reminders(db: Session):
    today = date.today(); created = 0; refreshed = 0
    rows = db.query(FDRRecord).all()
    for row in rows:
        if row.status in {"Released", "Renewed"}: continue
        days = (row.maturity_date - today).days
        for milestone in REMINDER_MILESTONES:
            due = row.maturity_date - timedelta(days=milestone)
            reminder = db.query(LifecycleReminder).filter_by(fdr_id=row.id, milestone_days=milestone).first()
            message = f"FDR {row.fdr_number}: {'overdue follow-up' if milestone < 0 else f'{milestone}-day maturity reminder'}; verify next action."
            if reminder is None:
                if days <= milestone:
                    db.add(LifecycleReminder(fdr_id=row.id, milestone_days=milestone, due_date=due, status="open", message=message)); created += 1
            elif reminder.due_date != due:
                reminder.due_date = due
                reminder.status = "open" if days <= milestone else "scheduled"
                reminder.resolved_at = None
                reminder.message = message
                refreshed += 1
            elif days <= milestone and reminder.status == "scheduled":
                reminder.status = "open"; reminder.resolved_at = None; refreshed += 1
    db.commit()
    return {"created": created, "refreshed": refreshed, "evaluated_fdrs": len(rows)}

@app.post("/api/v1/lifecycle/reminders/run")
def run_lifecycle_reminders(db: Session = Depends(get_db), actor=Depends(current_actor)):
    ensure_write(actor, {"scrutiny_officer", "approving_authority", "admin"})
    result = evaluate_lifecycle_reminders(db)
    log_event(db, actor["user"], "LIFECYCLE_REMINDERS_EVALUATED", "lifecycle", None, f"Created {result['created']}; refreshed {result['refreshed']}; no email/SMS was sent.")
    db.commit()
    return {**result, "milestones_days": list(REMINDER_MILESTONES), "delivery": "in-app only"}

# Automatic local scheduler: evaluates every hour; use an external durable scheduler in production.
_scheduler_started = False
def _reminder_scheduler_loop():
    while True:
        try:
            with SessionLocal() as scheduled_db:
                evaluate_lifecycle_reminders(scheduled_db)
        except Exception as exc:
            print(f"Lifecycle scheduler error: {exc}")
        time.sleep(3600)

def start_reminder_scheduler():
    global _scheduler_started
    if not _scheduler_started:
        threading.Thread(target=_reminder_scheduler_loop, name="fdr-reminder-scheduler", daemon=True).start()
        _scheduler_started = True

@app.patch("/api/v1/lifecycle/reminders/{reminder_id}/resolve")
def resolve_lifecycle_reminder(reminder_id: int, db: Session = Depends(get_db), actor=Depends(current_actor)):
    ensure_write(actor, {"scrutiny_officer", "approving_authority", "admin"})
    row = db.query(LifecycleReminder).filter_by(id=reminder_id).first()
    if not row: raise HTTPException(404, "Reminder not found")
    row.status = "resolved"; row.resolved_at = datetime.utcnow()
    log_event(db, actor["user"], "LIFECYCLE_REMINDER_RESOLVED", "lifecycle_reminder", str(row.id), f"FDR {row.fdr_id}; milestone {row.milestone_days}")
    db.commit(); return {"id": row.id, "status": row.status}

@app.post("/api/v1/fdrs/{fdr_id}/replacement", status_code=201)
def link_replacement(fdr_id: int, payload: dict, db: Session = Depends(get_db), actor=Depends(current_actor)):
    ensure_write(actor, {"institution_user", "scrutiny_officer", "admin"})
    predecessor = scoped_fdr(db, fdr_id, actor)
    successor_id = payload.get("replacement_fdr_id")
    successor = db.query(FDRRecord).filter_by(id=successor_id).first() if isinstance(successor_id, int) else None
    if not successor or (actor["role"] == "institution_user" and successor.institution_id != actor["institution_id"]): raise HTTPException(404, "Original or replacement FDR not found")
    if predecessor.id == successor.id: raise HTTPException(422, "An FDR cannot replace itself")
    if predecessor.institution_id != successor.institution_id: raise HTTPException(422, "Replacement must belong to the same institution")
    if successor.maturity_date <= predecessor.maturity_date: raise HTTPException(422, "Replacement maturity must be later than the original maturity")
    if db.query(LifecycleLink.id).filter_by(predecessor_fdr_id=fdr_id, successor_fdr_id=successor.id).first(): raise HTTPException(409, "Replacement link already exists")
    link = LifecycleLink(predecessor_fdr_id=fdr_id, successor_fdr_id=successor.id, link_type="replacement", notes=str(payload.get("notes", ""))[:1000], created_by=actor["user"])
    db.add(link); predecessor.status = "Renewed"; predecessor.updated_at = datetime.utcnow()
    log_event(db, actor["user"], "FDR_REPLACEMENT_LINKED", "fdr", str(fdr_id), f"Successor FDR {successor.id}; chain recorded")
    db.commit(); db.refresh(link)
    return {"id": link.id, "predecessor_fdr_id": link.predecessor_fdr_id, "successor_fdr_id": link.successor_fdr_id, "link_type": link.link_type, "created_by": link.created_by}

@app.get("/api/v1/fdrs/{fdr_id}/lifecycle")
def fdr_lifecycle(fdr_id: int, db: Session = Depends(get_db), actor=Depends(current_actor)):
    row = scoped_fdr(db, fdr_id, actor)
    links = db.query(LifecycleLink).filter(or_(LifecycleLink.predecessor_fdr_id == fdr_id, LifecycleLink.successor_fdr_id == fdr_id)).order_by(LifecycleLink.created_at.asc()).all()
    return {"fdr_id": fdr_id, "status": row.status, "days_to_maturity": (row.maturity_date-date.today()).days,
        "links": [{"id": x.id, "predecessor_fdr_id": x.predecessor_fdr_id, "successor_fdr_id": x.successor_fdr_id, "link_type": x.link_type, "notes": x.notes, "created_by": x.created_by, "created_at": x.created_at.isoformat()} for x in links]}

@app.get("/api/v1/release-requests")
def list_release_requests(status: str = "All", db: Session = Depends(get_db), actor=Depends(current_actor)):
    query = db.query(ReleaseRequest).join(FDRRecord, FDRRecord.id == ReleaseRequest.fdr_id).order_by(ReleaseRequest.created_at.desc())
    if actor["role"] == "institution_user": query = query.filter(FDRRecord.institution_id == actor["institution_id"])
    if status != "All": query = query.filter(ReleaseRequest.status == status)
    rows = query.limit(200).all()
    return [{"id": r.id, "fdr_id": r.fdr_id, "fdr_number": db.query(FDRRecord.fdr_number).filter_by(id=r.fdr_id).scalar(), "maker": r.maker, "checker": r.checker, "status": r.status, "reason": r.reason, "decision_note": r.decision_note, "created_at": r.created_at.isoformat(), "updated_at": r.updated_at.isoformat()} for r in rows]

@app.post("/api/v1/fdrs/{fdr_id}/release-requests", status_code=201)
def create_release_request(fdr_id: int, payload: dict, db: Session = Depends(get_db), actor=Depends(current_actor)):
    ensure_write(actor, {"institution_user", "scrutiny_officer", "admin"})
    row = db.query(FDRRecord).filter_by(id=fdr_id).first()
    if not row or (actor["role"] == "institution_user" and row.institution_id != actor["institution_id"]): raise HTTPException(404, "FDR record not found")
    if row.status in {"Released", "Renewed"}: raise HTTPException(409, "Released or renewed FDR cannot enter release workflow")
    if row.maturity_date > date.today(): raise HTTPException(409, "Release request is blocked until the FDR matures")
    if row.verification_status != "Confirmed": raise HTTPException(409, "Release request blocked: bank verification is not confirmed")
    open_request = db.query(ReleaseRequest).filter(ReleaseRequest.fdr_id == fdr_id, ReleaseRequest.status.in_(["requested", "approved", "processing"])).first()
    if open_request: raise HTTPException(409, "An open release request already exists")
    request = ReleaseRequest(fdr_id=fdr_id, maker=actor["user"], reason=str(payload.get("reason", "Release/refund requested"))[:1000], status="requested")
    db.add(request); log_event(db, actor["user"], "RELEASE_REQUEST_CREATED", "release_request", None, f"FDR {fdr_id}; awaiting independent approval")
    db.commit(); db.refresh(request); return {"id": request.id, "fdr_id": request.fdr_id, "maker": request.maker, "status": request.status, "reason": request.reason}

@app.post("/api/v1/release-requests/{request_id}/approve")
def approve_release_request(request_id: int, payload: dict, db: Session = Depends(get_db), actor=Depends(current_actor)):
    ensure_write(actor, {"approving_authority", "admin"})
    row = db.query(ReleaseRequest).filter_by(id=request_id).first()
    if not row: raise HTTPException(404, "Release request not found")
    if row.status != "requested": raise HTTPException(409, "Only requested items can be approved")
    if row.maker == actor["user"]: raise HTTPException(403, "Maker-checker rule: the requester cannot approve their own request")
    row.status = "approved"; row.checker = actor["user"]; row.decision_note = str(payload.get("note", "Approved for processing"))[:1000]; row.updated_at = datetime.utcnow()
    log_event(db, actor["user"], "RELEASE_REQUEST_APPROVED", "release_request", str(row.id), f"Independent checker approved FDR {row.fdr_id}")
    db.commit(); return {"id": row.id, "status": row.status, "maker": row.maker, "checker": row.checker}

@app.post("/api/v1/release-requests/{request_id}/process")
def process_release_request(request_id: int, payload: dict, db: Session = Depends(get_db), actor=Depends(current_actor)):
    ensure_write(actor, {"approving_authority", "admin"})
    row = db.query(ReleaseRequest).filter_by(id=request_id).first()
    if not row: raise HTTPException(404, "Release request not found")
    if row.status != "approved": raise HTTPException(409, "Request must be independently approved before processing")
    if row.maker == actor["user"]: raise HTTPException(403, "Requester cannot process their own request")
    row.status = "processing"; row.decision_note = str(payload.get("note", "Marked ready for offline disbursement"))[:1000]; row.updated_at = datetime.utcnow()
    log_event(db, actor["user"], "RELEASE_REQUEST_PROCESSING", "release_request", str(row.id), "Workflow marked processing; no money was moved")
    db.commit(); return {"id": row.id, "status": row.status, "note": "Workflow state updated only; no financial transaction occurred."}

@app.post("/api/v1/release-requests/{request_id}/close")
def close_release_request(request_id: int, payload: dict, db: Session = Depends(get_db), actor=Depends(current_actor)):
    ensure_write(actor, {"approving_authority", "admin"})
    row = db.query(ReleaseRequest).filter_by(id=request_id).first()
    if not row: raise HTTPException(404, "Release request not found")
    if row.status != "processing": raise HTTPException(409, "Only processing requests can be closed")
    if row.maker == actor["user"]: raise HTTPException(403, "Requester cannot close their own request")
    row.status = "closed"; row.decision_note = str(payload.get("note", "Closed after offline confirmation"))[:1000]; row.updated_at = datetime.utcnow()
    fdr = db.query(FDRRecord).filter_by(id=row.fdr_id).first(); fdr.status = "Released"; fdr.updated_at = datetime.utcnow()
    log_event(db, actor["user"], "RELEASE_REQUEST_CLOSED", "release_request", str(row.id), f"FDR {row.fdr_id} marked Released after workflow closure; no money moved by this application")
    db.commit(); return {"id": row.id, "status": row.status, "fdr_status": fdr.status, "note": "Record closed; no money was moved."}
