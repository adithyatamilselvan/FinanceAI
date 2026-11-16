"""Local OCR for payment slips: Tesseract (via pytesseract) + Pillow preprocessing + PyMuPDF for PDFs.

Everything runs in memory. No source file is ever written to disk by this module. Heavy imports (PIL, PyMuPDF,
pytesseract) happen on first use so application start-up stays light. No paid API, no ML framework.
"""
import io
import logging
import os
import re
import shutil
import threading
from dataclasses import dataclass, field

from ..config import get_settings

log = logging.getLogger("financeai.ocr")

ALLOWED_EXTENSIONS = {"jpg": "image", "jpeg": "image", "png": "image", "pdf": "pdf"}
ALLOWED_MIME = {"jpg": {"image/jpeg", "image/jpg", "image/pjpeg"}, "jpeg": {"image/jpeg", "image/jpg", "image/pjpeg"}, "png": {"image/png"}, "pdf": {"application/pdf"}}
_WINDOWS_DEFAULTS = (r"C:\Program Files\Tesseract-OCR\tesseract.exe", r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe")

_semaphore: threading.BoundedSemaphore | None = None
_sem_lock = threading.Lock()


class SlipFileError(ValueError):
    """The file cannot be processed; the message is safe to show to the user."""


class OcrUnavailable(RuntimeError):
    """The Tesseract binary is missing or unusable."""


@dataclass
class OcrResult:
    text: str
    confidence: float | None  # 0-100, None when no OCR was needed (digital PDF text)
    method: str  # ocr-image | pdf-text | pdf-ocr | pdf-text+ocr
    pages: int = 1
    warnings: list[str] = field(default_factory=list)


def find_tesseract() -> str | None:
    s = get_settings()
    candidates = [s.tesseract_cmd, os.environ.get("TESSERACT_CMD", ""), shutil.which("tesseract") or "", *_WINDOWS_DEFAULTS]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


def ocr_status() -> dict:
    """Cheap capability probe for the UI / health checks (does not run OCR)."""
    cmd = find_tesseract()
    try:
        import fitz  # noqa: F401
        import PIL  # noqa: F401
        import pytesseract  # noqa: F401

        libs = True
    except ImportError:
        libs = False
    s = get_settings()
    return {"available": bool(cmd and libs), "engine": "tesseract" if cmd else None, "reason": None if cmd and libs else ("Tesseract binary not found" if not cmd else "OCR Python libraries missing"),
            "limits": {"max_file_bytes": s.slip_max_file_bytes, "max_files_per_batch": s.slip_max_files_per_batch, "max_pdf_pages": s.slip_max_pdf_pages,
                       "allowed_extensions": sorted(ALLOWED_EXTENSIONS)}}


def _limiter() -> threading.BoundedSemaphore:
    global _semaphore
    with _sem_lock:
        if _semaphore is None:
            _semaphore = threading.BoundedSemaphore(max(1, get_settings().ocr_max_concurrency))
        return _semaphore


def sniff_kind(ext: str, data: bytes) -> str:
    """Validate that the bytes really are what the extension claims (magic numbers)."""
    ext = ext.lower()
    if ext in ("jpg", "jpeg") and data[:3] == b"\xff\xd8\xff":
        return "image"
    if ext == "png" and data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image"
    if ext == "pdf" and data[:5] == b"%PDF-":
        return "pdf"
    raise SlipFileError("The file content does not match its extension (expected a real JPG, PNG or PDF).")


def _prepare(img):
    """Grayscale, upscale small images, stretch contrast. Pillow only (no OpenCV needed for clean slips/screenshots)."""
    from PIL import Image, ImageFilter, ImageOps

    if img.mode not in ("L", "RGB"):
        img = img.convert("RGB")
    img = ImageOps.grayscale(img)
    w, h = img.size
    if max(w, h) > 3000:  # bound memory and OCR time
        r = 3000 / max(w, h)
        img = img.resize((int(w * r), int(h * r)), Image.LANCZOS)
    elif w < 1000:  # tiny screenshots OCR much better when enlarged
        r = 1000 / w
        img = img.resize((int(w * r), int(h * r)), Image.LANCZOS)
    img = ImageOps.autocontrast(img, cutoff=1)
    return img.filter(ImageFilter.SHARPEN)


def _tesseract(img, psm: int) -> tuple[str, float | None]:
    import pytesseract

    s = get_settings()
    cmd = find_tesseract()
    if not cmd:
        raise OcrUnavailable("OCR engine (Tesseract) is not installed on this server.")
    pytesseract.pytesseract.tesseract_cmd = cmd
    cfg = f"--oem 3 --psm {psm}"
    with _limiter():
        try:
            data = pytesseract.image_to_data(img, lang=s.ocr_lang, config=cfg, output_type=pytesseract.Output.DICT, timeout=s.ocr_timeout_seconds)
        except RuntimeError as e:  # pytesseract raises RuntimeError on timeout
            raise SlipFileError("Reading this file took too long. Try a smaller or clearer image.") from e
        except pytesseract.TesseractNotFoundError as e:
            raise OcrUnavailable("OCR engine (Tesseract) is not installed on this server.") from e
        except pytesseract.TesseractError as e:
            log.error("Tesseract failed: %s", type(e).__name__)
            raise SlipFileError("The OCR engine could not read this image.") from e
    lines: dict[tuple, list[str]] = {}
    confs = []
    for i, word in enumerate(data["text"]):
        w = word.strip()
        if not w:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        lines.setdefault(key, []).append(w)
        c = float(data["conf"][i])
        if c >= 0:
            confs.append(c)
    text = "\n".join(" ".join(v) for v in lines.values())
    return text, (sum(confs) / len(confs) if confs else None)


_AMOUNT_LIKE = re.compile(r"\d[\d,]*\.\d{2}|(?:rs\.?|inr|₹)\s*\d", re.I)


def _norm_line(line: str) -> str:
    return re.sub(r"[^a-z0-9]", "", line.lower())


def _merge_missing_lines(primary: str, extra: str) -> str:
    """Append lines that the second pass found but the first pass dropped (e.g. a large amount line starting with the rupee sign)."""
    have = {_norm_line(ln) for ln in primary.splitlines()}
    add = [ln for ln in extra.splitlines() if _norm_line(ln) and _norm_line(ln) not in have and len(_norm_line(ln)) >= 3]
    return primary + ("\n" + "\n".join(add) if add else "")


def ocr_image_obj(img) -> tuple[str, float | None]:
    """OCR one PIL image. Block layout first; if the result looks incomplete (no amount-like text, very short, or low confidence)
    a sparse-layout pass runs and any lines the first pass dropped are merged in. Two passes at most."""
    prepared = _prepare(img)
    text, conf = _tesseract(prepared, 6)
    if len(text.strip()) < 15 or (conf is not None and conf < 55) or not _AMOUNT_LIKE.search(text):
        t2, c2 = _tesseract(prepared, 11)  # sparse text: typical for centred amounts in app screenshots
        if len(text.strip()) < 15 and (c2 or 0) >= (conf or 0):
            text, conf = t2, c2
        else:
            text = _merge_missing_lines(text, t2)
            conf = conf if conf is not None else c2
    return text, conf


def _open_image(data: bytes):
    from PIL import Image, UnidentifiedImageError

    s = get_settings()
    try:
        probe = Image.open(io.BytesIO(data))
        probe.verify()  # structural check without decoding all pixels
        img = Image.open(io.BytesIO(data))
        if img.width * img.height > s.slip_max_image_pixels:
            raise SlipFileError(f"The image is too large ({img.width}x{img.height} pixels). Use a photo under {s.slip_max_image_pixels // 1_000_000} megapixels.")
        img.load()
        return img
    except SlipFileError:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError) as e:
        raise SlipFileError("The image is damaged or not a valid JPG/PNG file.") from e


def ocr_image_bytes(data: bytes) -> OcrResult:
    img = _open_image(data)
    try:
        text, conf = ocr_image_obj(img)
    finally:
        img.close()
    return OcrResult(text=text, confidence=conf, method="ocr-image")


def ocr_pdf_bytes(data: bytes) -> OcrResult:
    """Digital PDFs: direct text. Scanned pages (little/no text): render and OCR, one page at a time."""
    import fitz
    from PIL import Image

    s = get_settings()
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as e:  # PyMuPDF raises several types for malformed files
        raise SlipFileError("The PDF is damaged or not a valid PDF file.") from e
    try:
        if doc.needs_pass or doc.is_encrypted:
            raise SlipFileError("The PDF is password-protected. Remove the password and upload it again.")
        if doc.page_count == 0:
            raise SlipFileError("The PDF is damaged or has no pages.")
        if doc.page_count > s.slip_max_pdf_pages:
            raise SlipFileError(f"The PDF has {doc.page_count} pages; the limit is {s.slip_max_pdf_pages}.")
        texts, confs, used_text, used_ocr = [], [], False, False
        for page in doc:
            direct = page.get_text("text").strip()
            if len(direct) >= s.slip_pdf_min_text_chars:
                texts.append(direct)
                used_text = True
                continue
            pix = page.get_pixmap(dpi=200, alpha=False)  # rendered in memory, one page at a time
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            del pix
            try:
                t, c = ocr_image_obj(img)
            finally:
                img.close()
            texts.append(t)
            used_ocr = True
            if c is not None:
                confs.append(c)
        method = "pdf-text+ocr" if used_text and used_ocr else "pdf-ocr" if used_ocr else "pdf-text"
        return OcrResult(text="\n".join(texts), confidence=(sum(confs) / len(confs)) if confs else None, method=method, pages=doc.page_count)
    finally:
        doc.close()


def extract_text(ext: str, data: bytes) -> OcrResult:
    kind = sniff_kind(ext, data)
    return ocr_image_bytes(data) if kind == "image" else ocr_pdf_bytes(data)
