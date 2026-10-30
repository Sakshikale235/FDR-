from datetime import datetime, date
from sqlalchemy import Column, Integer, String, Date, DateTime, Float, ForeignKey, Text, Boolean
from sqlalchemy.orm import relationship
from .database import Base

class Institution(Base):
    __tablename__ = "institutions"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(180), nullable=False, unique=True, index=True)
    code = Column(String(40), nullable=False, unique=True, index=True)
    region = Column(String(80), default="Western")
    state = Column(String(80), default="Maharashtra")
    contact_email = Column(String(180), default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    fdrs = relationship("FDRRecord", back_populates="institution")

class FDRRecord(Base):
    __tablename__ = "fdr_records"
    id = Column(Integer, primary_key=True, index=True)
    fdr_number = Column(String(100), nullable=False, index=True)
    bank_name = Column(String(160), nullable=False)
    holder_name = Column(String(180), nullable=False)
    amount = Column(Float, nullable=False)
    issue_date = Column(Date, nullable=False)
    maturity_date = Column(Date, nullable=False, index=True)
    status = Column(String(40), default="Pending Verification", index=True)
    verification_status = Column(String(40), default="Pending")
    risk_level = Column(String(20), default="Low")
    notes = Column(Text, default="")
    institution_id = Column(Integer, ForeignKey("institutions.id"), nullable=False, index=True)
    created_by = Column(String(100), default="demo.admin")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    institution = relationship("Institution", back_populates="fdrs")
    documents = relationship("FDRDocument", back_populates="fdr", cascade="all, delete-orphan")

class FDRDocument(Base):
    __tablename__ = "fdr_documents"
    id = Column(Integer, primary_key=True, index=True)
    fdr_id = Column(Integer, ForeignKey("fdr_records.id"), nullable=False, index=True)
    filename = Column(String(255), nullable=False)
    stored_path = Column(String(500), nullable=False)
    sha256 = Column(String(64), nullable=False, index=True)
    size_bytes = Column(Integer, nullable=False)
    version = Column(Integer, default=1)
    uploaded_by = Column(String(100), default="demo.admin")
    uploaded_at = Column(DateTime, default=datetime.utcnow)
    is_current = Column(Boolean, default=True)
    fdr = relationship("FDRRecord", back_populates="documents")

class AuditEvent(Base):
    __tablename__ = "audit_events"
    id = Column(Integer, primary_key=True, index=True)
    actor = Column(String(100), nullable=False)
    action = Column(String(100), nullable=False)
    entity_type = Column(String(80), nullable=False)
    entity_id = Column(String(80), nullable=True)
    details = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

class ExtractionRun(Base):
    __tablename__ = "extraction_runs"
    id = Column(Integer, primary_key=True, index=True)
    fdr_id = Column(Integer, ForeignKey("fdr_records.id"), nullable=False, index=True)
    document_id = Column(Integer, ForeignKey("fdr_documents.id"), nullable=False, index=True)
    status = Column(String(30), nullable=False, default="completed")
    result_json = Column(Text, nullable=False, default="{}")
    quality_json = Column(Text, nullable=False, default="[]")
    created_by = Column(String(100), nullable=False, default="demo.admin")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

class FieldAssertion(Base):
    __tablename__ = "field_assertions"
    id = Column(Integer, primary_key=True, index=True)
    fdr_id = Column(Integer, ForeignKey("fdr_records.id"), nullable=False, index=True)
    document_id = Column(Integer, ForeignKey("fdr_documents.id"), nullable=True, index=True)
    field_name = Column(String(80), nullable=False, index=True)
    value = Column(Text, nullable=False)
    source_type = Column(String(40), nullable=False)  # OCR, institution, officer
    page = Column(Integer, nullable=True)
    region_json = Column(Text, nullable=False, default="{}")
    confidence = Column(Float, nullable=True)
    asserted_by = Column(String(100), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

class LifecycleLink(Base):
    __tablename__ = "lifecycle_links"
    id = Column(Integer, primary_key=True, index=True)
    predecessor_fdr_id = Column(Integer, ForeignKey("fdr_records.id"), nullable=False, index=True)
    successor_fdr_id = Column(Integer, ForeignKey("fdr_records.id"), nullable=False, index=True)
    link_type = Column(String(30), nullable=False, default="replacement")
    notes = Column(Text, default="")
    created_by = Column(String(100), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

class LifecycleReminder(Base):
    __tablename__ = "lifecycle_reminders"
    id = Column(Integer, primary_key=True, index=True)
    fdr_id = Column(Integer, ForeignKey("fdr_records.id"), nullable=False, index=True)
    milestone_days = Column(Integer, nullable=False)
    due_date = Column(Date, nullable=False, index=True)
    status = Column(String(20), nullable=False, default="open")
    message = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    resolved_at = Column(DateTime, nullable=True)

class ReleaseRequest(Base):
    __tablename__ = "release_requests"
    id = Column(Integer, primary_key=True, index=True)
    fdr_id = Column(Integer, ForeignKey("fdr_records.id"), nullable=False, index=True)
    maker = Column(String(100), nullable=False)
    checker = Column(String(100), nullable=True)
    status = Column(String(30), nullable=False, default="requested")
    reason = Column(Text, default="")
    decision_note = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(180), nullable=False, unique=True, index=True)
    password_hash = Column(String(256), nullable=False)
    full_name = Column(String(160), nullable=False)
    role = Column(String(40), nullable=False, index=True)
    institution_id = Column(Integer, ForeignKey("institutions.id"), nullable=True, index=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class IntakeUpload(Base):
    __tablename__ = "intake_uploads"
    id = Column(String(36), primary_key=True)
    filename = Column(String(255), nullable=False)
    stored_path = Column(String(500), nullable=False)
    sha256 = Column(String(64), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    extracted_json = Column(Text, nullable=False, default="{}")
    uploaded_by = Column(String(180), nullable=False)
    institution_id = Column(Integer, ForeignKey("institutions.id"), nullable=True)
    consumed = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
