# apps/documents/models.py

from django.db import models
from apps.tenants.models import Tenant


class TenantLetterhead(models.Model):
    """
    One letterhead config per tenant.

    The tenant uploads a blank A4 PDF — their pre-designed stationery
    (logo, address bar, borders, watermark, footer already printed on it)
    with the CENTRE of the page left white/empty.

    Your system renders quotation content into that white space using
    fixed standard margins (45 mm top, 28 mm bottom, 18 mm left/right).
    Tell every tenant: "Design your header to fit in the top 45 mm and
    your footer to fit in the bottom 28 mm — leave the middle blank."

    Company info here fills the "From" block in every quotation PDF.
    """

    tenant = models.OneToOneField(
        Tenant,
        on_delete=models.CASCADE,
        related_name='letterhead',
    )

    # The tenant's blank letterhead PDF
    letterhead_pdf = models.FileField(
        upload_to='letterheads/pdfs/',
        null=True,
        blank=True,
        help_text=(
            "Blank A4 PDF with the company's pre-designed stationery. "
            "Header must fit within the top 45 mm; footer within the "
            "bottom 28 mm. Leave the middle of each page white/empty."
        ),
    )

    # ── Company info ─────────────────────────────────────────────────────────
    # Used in the "From" block of the quotation document.
    # These override whatever is on the Tenant model if set.
    company_name    = models.CharField(max_length=255, blank=True)
    company_address = models.TextField(blank=True)
    company_phone   = models.CharField(max_length=50,  blank=True)
    company_email   = models.EmailField(blank=True)
    company_gstin   = models.CharField(max_length=20,  blank=True)
    company_pan     = models.CharField(max_length=20,  blank=True)
    company_state   = models.CharField(
        max_length=100, blank=True,
        help_text="Used to determine CGST/SGST (intra-state) vs IGST (inter-state).",
    )

    # ── Bank details — printed at the bottom of every quotation PDF ──────────
    bank_name           = models.CharField(max_length=255, blank=True,
                            help_text="e.g. State Bank of India")
    bank_account_name   = models.CharField(max_length=255, blank=True,
                            help_text="Account holder name as per bank records")
    bank_branch         = models.CharField(max_length=255, blank=True,
                            help_text="Branch name / code")
    bank_account_number = models.CharField(max_length=50,  blank=True,
                            help_text="Bank account number")
    bank_ifsc           = models.CharField(max_length=20,  blank=True,
                            help_text="IFSC code")
    bank_micr           = models.CharField(max_length=20,  blank=True,
                            help_text="MICR code (optional)")

    # Accent colour for table headers inside the PDF
    accent_color = models.CharField(
        max_length=7, default='#122C41',
        help_text="Hex colour for table headers, e.g. #122C41",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Tenant Letterhead'

    def __str__(self):
        return f"Letterhead — {self.tenant.company_name}"