"""Advisory-only OCR extraction helpers. Extraction never changes verification status."""
import re
from pathlib import Path
from datetime import datetime

FIELD_PATTERNS = {
    "fdr_number": [r"(?:FDR\s*(?:No\.?|Number|#)|Fixed\s*Deposit\s*(?:Receipt\s*)?(?:No\.?|Number|#))\s*[:\-]?\s*([A-Z0-9\-/]{5,})"],
    "bank_name": [r"\b(Canara Bank|State Bank of India|SBI|HDFC Bank|ICICI Bank|Axis Bank|Bank of Baroda|Punjab National Bank|Union Bank of India|Indian Bank|IDBI Bank|Bank of India)\b"],
    "holder_name": [r"(?:Name of (?:the )?Depositor|Depositor|Account Holder|Holder Name|In favour of)\s*[:\-]?\s*([A-Z][A-Za-z0-9 &.,'()-]{2,100})"],
    "amount": [r"(?:Amount|Deposit Amount|Principal Amount|Sum of)\s*[:\-]?\s*(?:INR|Rs\.?|₹)?\s*([\d,]+(?:\.\d{1,2})?)", r"₹\s*([\d,]+(?:\.\d{1,2})?)"],
    "issue_date": [r"(?:Issue Date|Date of Issue|Date of Deposit)\s*[:\-]?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2}|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})"],
    "maturity_date": [r"(?:Maturity Date|Date of Maturity|Due Date)\s*[:\-]?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2}|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})"],
}

def _normalise_date(value):
    value = value.strip().replace('/', '-')
    for fmt in ("%d-%m-%Y", "%d-%m-%y", "%Y-%m-%d", "%d %B %Y", "%d %b %Y"):
        try: return datetime.strptime(value, fmt).date().isoformat()
        except ValueError: pass
    return value

def extract_document(path: str):
    """Return field candidates with page-level evidence. OCR is optional and degrades gracefully."""
    file_path = Path(path)
    pages = []
    metadata = {}
    engine_used = "none"
    if file_path.suffix.lower() == ".pdf":
        try:
            import fitz
            doc = fitz.open(file_path)
            metadata = doc.metadata or {}
            for idx, page in enumerate(doc):
                text = page.get_text("text") or ""
                words = page.get_text("words") or []
                if not text.strip():
                    try:
                        import pytesseract
                        from PIL import Image
                        import io
                        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                        text = pytesseract.image_to_string(Image.open(io.BytesIO(pix.tobytes("png"))))
                        engine_used = "tesseract"
                    except Exception:
                        pass
                elif engine_used == "none": engine_used = "pymupdf-text"
                pages.append({"page": idx + 1, "text": text, "words": words, "page_obj": page})
        except Exception as exc:
            return {"status": "needs_manual_review", "engine": "unavailable", "fields": {}, "quality_signals": [f"PDF could not be parsed ({type(exc).__name__}); manual review required."], "message": "Extraction is unavailable for this file. Upload a readable PDF or review manually."}
    elif file_path.suffix.lower() in {".png", ".jpg", ".jpeg"}:
        try:
            import pytesseract
            from PIL import Image
            text = pytesseract.image_to_string(Image.open(file_path))
            engine_used = "tesseract"
            pages = [{"page": 1, "text": text, "words": [], "page_obj": None}]
        except Exception:
            return {"status": "needs_manual_review", "engine": "unavailable", "fields": {}, "quality_signals": ["Image OCR engine is unavailable; install Tesseract and pytesseract or review manually."], "message": "No extraction was produced. This is not a verification failure."}
    else:
        return {"status": "needs_manual_review", "engine": "unsupported", "fields": {}, "quality_signals": ["Unsupported document type."], "message": "Manual review required."}

    all_text = "\n".join(p["text"] for p in pages)
    fields = {}
    for field, patterns in FIELD_PATTERNS.items():
        found = None
        for page in pages:
            for pattern in patterns:
                match = re.search(pattern, page["text"], flags=re.IGNORECASE)
                if match:
                    value = match.group(1).strip().strip(" .,:;")
                    if field in {"issue_date", "maturity_date"}: value = _normalise_date(value)
                    if field == "amount": value = value.replace(",", "")
                    # Confidence is a heuristic extraction confidence, not probability of authenticity.
                    confidence = 0.92 if page["words"] else (0.78 if len(value) >= 4 else 0.60)
                    region = None
                    if page.get("page_obj") is not None:
                        try:
                            rects = page["page_obj"].search_for(match.group(0), quads=False)
                            if rects:
                                rect = rects[0]
                                region = {"x0": round(rect.x0, 2), "y0": round(rect.y0, 2), "x1": round(rect.x1, 2), "y1": round(rect.y1, 2)}
                        except Exception:
                            region = None
                    found = {"value": value, "confidence": confidence, "evidence": {"page": page["page"], "region": region, "text_excerpt": match.group(0)[:180]}}
                    break
            if found: break
        fields[field] = found or {"value": None, "confidence": 0.0, "evidence": None}
    quality = []
    if not all_text.strip(): quality.append("No readable text detected; manual field entry is required.")
    if len(all_text.strip()) < 80: quality.append("Very little text was extracted; inspect the scan quality.")
    if metadata.get("modDate") and metadata.get("creationDate") and metadata["modDate"] != metadata["creationDate"]:
        quality.append("PDF modification metadata differs from creation metadata. Review signal only; not an authenticity verdict.")
    if not fields["fdr_number"]["value"]: quality.append("FDR number was not confidently located.")
    if not fields["amount"]["value"]: quality.append("Deposit amount was not confidently located.")
    try:
        if file_path.suffix.lower() == ".pdf": doc.close()
    except Exception:
        pass
    return {"status": "completed" if any(v["value"] is not None for v in fields.values()) else "needs_manual_review", "engine": engine_used, "fields": fields, "quality_signals": quality, "message": "Extracted values are candidates only. OCR, hashes and quality signals do not authenticate an FDR."}
