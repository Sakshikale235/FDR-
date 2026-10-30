from datetime import date, datetime
from pydantic import BaseModel, ConfigDict, Field

class InstitutionCreate(BaseModel):
    name: str = Field(min_length=2, max_length=180)
    code: str = Field(min_length=2, max_length=40)
    region: str = "Western"
    state: str = "Maharashtra"
    contact_email: str = ""

class InstitutionOut(InstitutionCreate):
    id: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

class FDRCreate(BaseModel):
    fdr_number: str = Field(min_length=2, max_length=100)
    bank_name: str = Field(min_length=2, max_length=160)
    holder_name: str = Field(min_length=2, max_length=180)
    amount: float = Field(gt=0)
    issue_date: date
    maturity_date: date
    institution_id: int
    notes: str = ""

class FDROut(BaseModel):
    id: int
    fdr_number: str
    bank_name: str
    holder_name: str
    amount: float
    issue_date: date
    maturity_date: date
    status: str
    verification_status: str
    risk_level: str
    notes: str
    institution_id: int
    institution_name: str
    document_count: int = 0
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

class StatusUpdate(BaseModel):
    status: str
    verification_status: str | None = None
    risk_level: str | None = None
