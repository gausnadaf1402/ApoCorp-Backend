# apps/documents/pdf_engine.py
#
# Two-stage PDF generation:
#   Stage 1 — Playwright (headless Chromium) renders quotation HTML → content PDF
#   Stage 2 — PyMuPDF overlays content PDF on every page of the tenant's
#              letterhead PDF (their blank-centre stationery).
#
# Fixed standard margins (mm) — tell tenants to design within these:
#   Top    : 58 mm   (header + logo must fit here)
#   Bottom : 28 mm   (footer must fit here)
#   Left   : 18 mm
#   Right  : 18 mm
#
# If a tenant has not yet uploaded a letterhead PDF, Stage 2 is skipped
# and the content-only PDF is returned — still fully professional.
#
# INSTALL:
#   pip install playwright pymupdf
#   playwright install chromium          ← one-time, downloads ~170 MB

from __future__ import annotations

import io
from decimal import Decimal

# ── Fixed layout constants ────────────────────────────────────────────────────

MARGIN_TOP_MM    = 58
MARGIN_BOTTOM_MM = 28
MARGIN_LEFT_MM   = 18
MARGIN_RIGHT_MM  = 18

# ─────────────────────────────────────────────────────────────────────────────
# Amount-in-words  (Indian numbering system)
# ─────────────────────────────────────────────────────────────────────────────

_ONES = [
    '', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight',
    'Nine', 'Ten', 'Eleven', 'Twelve', 'Thirteen', 'Fourteen', 'Fifteen',
    'Sixteen', 'Seventeen', 'Eighteen', 'Nineteen',
]
_TENS = ['', '', 'Twenty', 'Thirty', 'Forty', 'Fifty',
         'Sixty', 'Seventy', 'Eighty', 'Ninety']


def _words_lt_1000(n: int) -> str:
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + (f' {_ONES[n % 10]}' if n % 10 else '')
    return (_ONES[n // 100] + ' Hundred'
            + (f' {_words_lt_1000(n % 100)}' if n % 100 else ''))


def amount_in_words(amount: Decimal, currency: str = 'INR') -> str:
    try:
        amount = Decimal(str(amount))
    except Exception:
        return ''

    integer_part = int(amount)
    paise_part   = int(round((amount - integer_part) * 100))

    if integer_part == 0:
        words = 'Zero'
    else:
        parts  = []
        crore  = integer_part // 10_000_000
        lakh   = (integer_part % 10_000_000) // 100_000
        thou   = (integer_part % 100_000)     // 1_000
        rest   = integer_part % 1_000

        if crore: parts.append(f'{_words_lt_1000(crore)} Crore')
        if lakh:  parts.append(f'{_words_lt_1000(lakh)} Lakh')
        if thou:  parts.append(f'{_words_lt_1000(thou)} Thousand')
        if rest:  parts.append(_words_lt_1000(rest))
        words = ' '.join(parts)

    suffix = 'Rupees' if currency == 'INR' else currency
    result = f'{words} {suffix}'
    if paise_part:
        result += f' and {_words_lt_1000(paise_part)} Paise'
    return result + ' Only'


# ─────────────────────────────────────────────────────────────────────────────
# GST split helper
# ─────────────────────────────────────────────────────────────────────────────

def split_gst(line_items, customer_state: str, company_state: str):
    """
    Intra-state  → CGST + SGST (each = total_tax / 2)
    Inter-state  → IGST = total_tax

    Returns (cgst, sgst, igst, cgst_rate, sgst_rate, igst_rate) as Decimal.
    """
    total_tax = sum(
        Decimal(str(getattr(item, 'tax_amount', 0) or 0))
        for item in line_items
    )
    subtotal = sum(
        Decimal(str(getattr(item, 'quantity', 0) or 0)) *
        Decimal(str(getattr(item, 'unit_price', 0) or 0))
        for item in line_items
    )

    eff_rate = (
        ((total_tax / subtotal) * 100).quantize(Decimal('0.01'))
        if subtotal else Decimal('0')
    )

    cust  = (customer_state or '').strip().lower()
    comp  = (company_state  or '').strip().lower()
    intra = bool(cust and comp and cust == comp)

    if intra:
        half      = (total_tax / 2).quantize(Decimal('0.01'))
        half_rate = (eff_rate / 2).quantize(Decimal('0.01'))
        return half, half, Decimal('0'), half_rate, half_rate, Decimal('0')
    else:
        return Decimal('0'), Decimal('0'), total_tax, Decimal('0'), Decimal('0'), eff_rate


# ─────────────────────────────────────────────────────────────────────────────
# Stage 1: HTML → content PDF bytes via Playwright (headless Chromium)
# ─────────────────────────────────────────────────────────────────────────────

def render_content_pdf(html: str, base_url: str) -> bytes:
    """
    Renders the HTML string to a PDF using a headless Chromium browser.

    The @page margins in your quotation.html CSS are respected exactly
    as they are — Playwright/Chromium honours @page rules natively.

    base_url is used to resolve any relative asset URLs (fonts, images)
    in the HTML. For Django, pass request.build_absolute_uri('/').
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise ImportError(
            'Playwright not installed.\n'
            'Run: pip install playwright && playwright install chromium'
        )

    with sync_playwright() as p:
        browser = p.chromium.launch(
            # These args are required when running inside a container /
            # Azure App Service sandbox (no GPU, no sandbox process).
            # They are safe to use on local dev too.
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-gpu',
            ]
        )
        page = browser.new_page()

        # Use goto() with a data URI — works on all Playwright versions.
        # Inject a <base> tag so relative asset URLs resolve against base_url.
        import base64
        if '<head>' in html:
            html = html.replace('<head>', f'<head><base href="{base_url}">', 1)
        encoded = base64.b64encode(html.encode('utf-8')).decode('ascii')
        page.goto(f'data:text/html;base64,{encoded}', wait_until='networkidle')

        pdf_bytes = page.pdf(
            format='A4',
            print_background=True,   # renders background colours & images
            # Margins are already declared in @page CSS inside quotation.html.
            # Setting margin here to 0 lets the CSS @page rule take full control
            # and avoids double-margin. If you ever remove the @page block from
            # the HTML, uncomment the lines below instead.
            margin={
                'top':    '0mm',
                'bottom': '0mm',
                'left':   '0mm',
                'right':  '0mm',
            },
        )

        browser.close()

    return pdf_bytes


# ─────────────────────────────────────────────────────────────────────────────
# Stage 2: overlay content PDF onto letterhead PDF via PyMuPDF
# ─────────────────────────────────────────────────────────────────────────────

def overlay_on_letterhead(content_pdf_bytes: bytes,
                           letterhead_pdf_path: str) -> bytes:
    """
    For each page of content_pdf_bytes, stamp it on top of the matching
    page of the tenant's letterhead PDF.

    If the letterhead has fewer pages than the content, the last
    letterhead page is reused for overflow content pages.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        raise ImportError('PyMuPDF not installed. Run: pip install pymupdf')

    lh_doc      = fitz.open(letterhead_pdf_path)
    content_doc = fitz.open(stream=content_pdf_bytes, filetype='pdf')
    lh_count    = len(lh_doc)
    out_doc     = fitz.open()

    for page_idx in range(len(content_doc)):
        lh_page_idx = min(page_idx, lh_count - 1)
        lh_page     = lh_doc[lh_page_idx]
        lh_rect     = lh_page.rect          # A4: Rect(0, 0, 595, 842) in points

        out_page = out_doc.new_page(width=lh_rect.width, height=lh_rect.height)

        # Layer 1 — letterhead (background stationery)
        out_page.show_pdf_page(lh_rect, lh_doc, lh_page_idx)

        # Layer 2 — quotation content (on top, transparent background)
        out_page.show_pdf_page(lh_rect, content_doc, page_idx)

    buf = io.BytesIO()
    out_doc.save(buf)
    buf.seek(0)
    return buf.getvalue()


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

def generate_quotation_pdf(html: str,
                            base_url: str,
                            letterhead_pdf_path: str | None) -> bytes:
    """
    Full pipeline:
      1. Render HTML → content PDF (Playwright / headless Chromium)
      2. If letterhead PDF exists → overlay content on it (PyMuPDF)
         Otherwise → return content-only PDF

    Falls back to content-only PDF if overlay fails (logs a warning).
    """
    content_bytes = render_content_pdf(html, base_url)

    if letterhead_pdf_path:
        try:
            return overlay_on_letterhead(content_bytes, letterhead_pdf_path)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning(
                'Letterhead overlay failed: %s — returning content-only PDF.', exc
            )

    return content_bytes