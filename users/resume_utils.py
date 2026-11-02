"""
Resume text extraction.

Order of attempts:
  1. pdfplumber  - fast, works on text-based PDFs
  2. PyPDF2      - occasional rescue for PDFs pdfplumber chokes on
  3. OCR         - pdf2image + pytesseract for scanned PDFs
  4. OCR         - pytesseract directly for image resumes (jpg/png)

Tesseract and Poppler locations come from settings (which read them from
environment variables) so the project is not hard-wired to one Windows
machine. If a step's dependency is missing we log and move on rather than
crashing the upload.
"""

import logging
import os

from django.conf import settings

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.bmp', '.tif', '.tiff', '.webp'}


def _configure_tesseract():
    """Point pytesseract at the binary, if a path was configured."""
    try:
        import pytesseract
    except ImportError:
        return None

    cmd = getattr(settings, 'TESSERACT_CMD', '') or os.environ.get(
        'TESSERACT_CMD', '')
    if cmd and os.path.exists(cmd):
        pytesseract.pytesseract.tesseract_cmd = cmd
    return pytesseract


def _extract_with_pdfplumber(path):
    try:
        import pdfplumber
    except ImportError:
        logger.info('pdfplumber is not installed - skipping.')
        return ''

    text = ''
    try:
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                extracted = page.extract_text()
                if extracted:
                    text += extracted + '\n'
    except Exception as exc:                      # noqa: BLE001
        logger.warning('pdfplumber failed on %s: %s', path, exc)
    return text


def _extract_with_pypdf(path):
    try:
        from PyPDF2 import PdfReader
    except ImportError:
        return ''

    text = ''
    try:
        reader = PdfReader(path)
        for page in reader.pages:
            extracted = page.extract_text()
            if extracted:
                text += extracted + '\n'
    except Exception as exc:                      # noqa: BLE001
        logger.warning('PyPDF2 failed on %s: %s', path, exc)
    return text


def _ocr_pdf(path):
    pytesseract = _configure_tesseract()
    if pytesseract is None:
        logger.info('pytesseract is not installed - skipping OCR.')
        return ''

    try:
        from pdf2image import convert_from_path
    except ImportError:
        logger.info('pdf2image is not installed - skipping PDF OCR.')
        return ''

    poppler_path = getattr(settings, 'POPPLER_PATH', '') or os.environ.get(
        'POPPLER_PATH', '')

    text = ''
    try:
        kwargs = {'dpi': 200}
        if poppler_path:
            kwargs['poppler_path'] = poppler_path
        for image in convert_from_path(path, **kwargs):
            text += pytesseract.image_to_string(image) + '\n'
    except Exception as exc:                      # noqa: BLE001
        logger.warning('PDF OCR failed on %s: %s', path, exc)
    return text


def _ocr_image(path):
    pytesseract = _configure_tesseract()
    if pytesseract is None:
        return ''

    try:
        from PIL import Image
    except ImportError:
        logger.info('Pillow is not installed - skipping image OCR.')
        return ''

    try:
        with Image.open(path) as image:
            return pytesseract.image_to_string(image)
    except Exception as exc:                      # noqa: BLE001
        logger.warning('Image OCR failed on %s: %s', path, exc)
        return ''


def extract_resume_text(path):
    """
    Return the best text we can get out of a resume file.

    Never raises - returns '' when every strategy fails.
    """
    if not path or not os.path.exists(path):
        logger.warning('Resume file not found: %s', path)
        return ''

    extension = os.path.splitext(path)[1].lower()

    if extension in IMAGE_EXTENSIONS:
        return _ocr_image(path).strip()

    if extension == '.txt':
        try:
            with open(path, 'r', encoding='utf-8', errors='ignore') as handle:
                return handle.read().strip()
        except OSError as exc:
            logger.warning('Could not read %s: %s', path, exc)
            return ''

    # Assume PDF from here on.
    text = _extract_with_pdfplumber(path)

    if not text.strip():
        text = _extract_with_pypdf(path)

    if not text.strip():
        text = _ocr_pdf(path)

    return text.strip()
