"""
recruitment/resume_utils.py

Shared resume-text extraction, used by both the email intake pipeline
(recruitment/intake.py) and AI candidate scoring (recruitment/ai_scoring.py).
"""

import fitz


def extract_resume_text(pdf_bytes: bytes) -> str:
    """
    Extract plain text from a PDF resume using PyMuPDF.
    """
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        return "\n".join(page.get_text() for page in doc)
