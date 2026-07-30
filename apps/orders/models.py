import uuid
from django.db import models
from django.utils import timezone
from django.contrib.auth.models import User
from core.mixins import TenantModelMixin
from apps.quotations.models import Quotation


class OrderAcknowledgement(TenantModelMixin):

    STATUS_CHOICES = [
        ('PENDING', 'Pending'),      # Auto-created when Generate OA is clicked, pre-filled
        ('DRAFT', 'Draft'),          # User has saved edits at least once
        ('CONVERTED', 'Converted'),  # Shared — Order created
        ('CANCELLED', 'Cancelled'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    oa_number = models.CharField(max_length=50, unique=True)

    # ── Live FK chain: oa → quotation → enquiry → customer ──
    quotation = models.OneToOneField(
        Quotation, on_delete=models.CASCADE, related_name='oa'
    )

    # ── Intentional snapshots — confirmed address at order time ──
    billing_snapshot = models.JSONField(null=True, blank=True)
    shipping_snapshot = models.JSONField(null=True, blank=True)

    # Transport details are OA-specific (agreed per order)
    transport_details = models.JSONField(null=True, blank=True)

    # Financial values — updated on every save (not locked)
    currency = models.CharField(max_length=10, default="INR")
    exchange_rate = models.DecimalField(max_digits=12, decimal_places=4, default=1)
    total_value = models.DecimalField(max_digits=15, decimal_places=2, default=0)

    is_cancelled = models.BooleanField(default=False)
    cancellation_reason = models.TextField(blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    last_activity_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self.last_activity_at:
            self.last_activity_at = timezone.now()
        if self.is_cancelled and not self.cancelled_at:
            self.cancelled_at = timezone.now()
            self.status = "CANCELLED"

        # Auto-generate OA number if not set
        if not self.oa_number:
            if self.quotation and self.quotation.quotation_number:
                self.oa_number = f"OA-{self.quotation.quotation_number}"
            else:
                timestamp = timezone.now().strftime('%Y%m%d%H%M%S')
                self.oa_number = f"OA-{timestamp}"

        super().save(*args, **kwargs)

    @property
    def customer(self):
        return self.quotation.enquiry.customer

    @property
    def enquiry(self):
        return self.quotation.enquiry


class OALineItem(models.Model):

    oa = models.ForeignKey(
        OrderAcknowledgement, on_delete=models.CASCADE, related_name='line_items'
    )

    job_code = models.CharField(max_length=100, blank=True)
    customer_part_no = models.CharField(max_length=100, blank=True)
    part_no = models.CharField(max_length=100, blank=True)
    description = models.TextField(blank=True)
    hsn_code = models.CharField(max_length=50, blank=True)
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    unit = models.CharField(max_length=50, blank=True)
    unit_price = models.DecimalField(max_digits=15, decimal_places=2)

    # Tax fields — stored so calculations survive save/reload
    tax_group_code = models.CharField(max_length=50, blank=True)
    tax_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0)

    # total = unit_price * quantity + tax_amount (incl. tax)
    total = models.DecimalField(max_digits=15, decimal_places=2, default=0)


class OACommercialTerms(models.Model):
    """
    Mirrors the "Commercial Terms & Conditions" printed form, clause by
    clause. Each clause on the form is a dropdown of canned options plus
    (usually) one free-editable option — the frontend sends whichever
    text the user picked/typed, and the corresponding field here just
    needs to be wide enough to hold it. Numeric fill-ins that go with a
    specific option (a %, a day count, a week count) get their own
    field so they can be used in calculations / templating, rather than
    being buried inside a sentence.
    """

    oa = models.OneToOneField(
        OrderAcknowledgement, on_delete=models.CASCADE, related_name='commercial_terms'
    )

    # ── 1) Price Basis ──
    # e.g. "Ex Works Pune" / "FOR Site" / "FOR Customer's Works/Godown" /
    # "FOB Mumbai Port" / free-edited text
    price_basis = models.CharField(max_length=255, blank=True)

    # ── 2) Packing & Forwarding ──
    # e.g. "Extra" / "Standard Packing included in above price" /
    # "Fumigation & Seaworthy packing Included"
    packing_forwarding = models.CharField(max_length=255, blank=True)
    # only meaningful when packing_forwarding is the "Extra - _%" option
    packing_extra_percentage = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )

    # ── 3) Freight ──
    freight_charges = models.CharField(max_length=255, blank=True)

    # ── 4) Insurance ──
    insurance = models.CharField(max_length=255, blank=True)

    # ── 5) GST ──
    # e.g. "Not Applicable" / "Extra as applicable to Customer account"
    gst_terms = models.CharField(max_length=255, blank=True)

    # ── 6) Drawing Approval / Manufacturing Clearance ──
    drawing_approval = models.CharField(max_length=255, blank=True)

    # ── 7) Delivery ──
    # "Ordered material shall be made ready within __ Weeks from <trigger>"
    delivery_period_weeks = models.PositiveIntegerField(null=True, blank=True)
    # the <trigger> text, e.g. "Receipt of technically and commercially
    # clear Purchase order" / "Receipt of Advance" / "Receipt of Advance,
    # Drawing Approval in CAT-1 and Manufacturing clearance"
    delivery_basis = models.CharField(max_length=255, blank=True)

    # ── 8) Inspection & Dispatch Clearance ──
    inspection = models.CharField(max_length=255, blank=True)
    dispatch_clearance = models.CharField(max_length=255, blank=True)

    # ── 9) Warranty ──
    # Full boilerplate paragraph — was CharField(255), too short to hold
    # even the standard clause, switched to TextField.
    warranty = models.TextField(blank=True)

    # ── 10) Terms of Payment ──
    # The form allows several milestones to apply at once (e.g. 20% with
    # PO + 70% against proforma invoice + 10% within 30 days of invoice),
    # so a single CharField can't represent it. Structured list instead:
    #   [
    #     {"option": "with_po", "percentage": 20},
    #     {"option": "drawing_approval", "percentage": 10},
    #     {"option": "proforma_invoice", "percentage": 60},
    #     {"option": "credit_days", "percentage": 10, "days_after_invoice": 30},
    #     {"option": "letter_of_credit", "percentage": 0, "lc_usance_days": 60},
    #     {"option": "custom", "label": "<free text from the editable slot>"}
    #   ]
    # Validated in the serializer (see OACommercialTermsSerializer).
    payment_milestones = models.JSONField(default=list, blank=True)
    # Free-text human-readable summary / fallback, and quick-access copies
    # of the two most commonly queried milestones (kept for backward
    # compatibility with anything already reading these two fields).
    payment_terms = models.TextField(blank=True)
    advance_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    days_after_invoicing = models.PositiveIntegerField(default=0)

    # ── 11) Technical Specifications & Bill of Material ──
    # Boilerplate clause; field only needed if a specific OA overrides
    # the standard wording.
    technical_spec_note = models.TextField(blank=True)

    # ── 12) Erection Guidelines ──
    # e.g. "Not Applicable" / "1 visit for 2 days is included in above price"
    erection_guidelines = models.CharField(max_length=255, blank=True)

    # ── 13) Commissioning Assistance ──
    commissioning_support = models.TextField(blank=True)
    commissioning_visits = models.PositiveIntegerField(null=True, blank=True)
    commissioning_days = models.PositiveIntegerField(null=True, blank=True)

    # ── Special Notes ──
    # Free list of the checked/editable special-note lines, e.g.
    # ["We shall submit Advance Bank Guarantee.",
    #  "We shall submit Performance Bank Guarantee for 10% Basic PO Value."]
    special_notes = models.JSONField(default=list, blank=True)
    directors_indemnity_percentage = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    pbg_percentage = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )

    # ── Existing document/format references (unchanged) ──
    ld_clause = models.CharField(max_length=100, blank=True)
    test_certificate = models.CharField(max_length=100, blank=True)
    abg_format = models.CharField(max_length=100, blank=True)
    pbg_format = models.CharField(max_length=100, blank=True)
    sd_format = models.CharField(max_length=100, blank=True)

    # ── 15-21 Extra Commercial Terms ──
    price_validity = models.CharField(max_length=255, blank=True)
    po_cancellation = models.CharField(max_length=255, blank=True)
    ordering_info = models.TextField(blank=True)
    force_majeure = models.TextField(blank=True)
    cost_over_run = models.CharField(max_length=255, blank=True)
    late_delivery_charges = models.CharField(max_length=255, blank=True)
    country_origin_material_certs = models.CharField(max_length=255, blank=True)

    schedule_dispatch_date = models.DateField(null=True, blank=True)

    # ── Financial totals (unchanged) ──
    net_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    igst = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    cgst = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    sgst = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0)

    # ── Channel / consultant (unchanged) ──
    channel_partner_name = models.CharField(max_length=255, blank=True)
    consultant_name = models.CharField(max_length=255, blank=True)

    commission_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    commission_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    consultant_charges = models.DecimalField(max_digits=15, decimal_places=2, default=0)


class Order(TenantModelMixin):

    STATUS_CHOICES = [
        ('HOLD', 'Hold'),
        ('IN_PROGRESS', 'In Progress'),
        ('COMPLETED', 'Completed'),
    ]

    STAGE_CHOICES = [
        ('PLANNING', 'Planning'),
        ('ENGINEERING', 'Engineering'),
        ('PRODUCTION', 'Production'),
        ('QA', 'QA'),
        ('DISPATCH', 'Dispatch'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order_number = models.CharField(max_length=50, unique=True)

    oa = models.OneToOneField(
        OrderAcknowledgement, on_delete=models.CASCADE, related_name='order'
    )

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='IN_PROGRESS')
    stage = models.CharField(max_length=20, choices=STAGE_CHOICES, default='PLANNING')

    order_category = models.CharField(
        max_length=20,
        choices=[
            ('DOMESTIC', 'Domestic'),
            ('INTERNATIONAL', 'International / Export'),
        ],
        default='DOMESTIC'
    )

    invoice_status = models.CharField(
        max_length=20,
        choices=[
            ('NOT_INVOICED', 'Not Invoiced'),
            ('PARTIALLY_INVOICED', 'Partially Invoiced'),
            ('FULLY_INVOICED', 'Fully Invoiced'),
        ],
        default='NOT_INVOICED'
    )

    currency = models.CharField(max_length=10)
    exchange_rate = models.DecimalField(max_digits=12, decimal_places=4, default=1)
    total_value = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    advance_paid = models.DecimalField(max_digits=15, decimal_places=2, default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.order_number


class OAAttachment(models.Model):
    oa = models.ForeignKey(
        OrderAcknowledgement, on_delete=models.CASCADE, related_name='attachments'
    )
    file = models.FileField(upload_to="oa_files/")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Attachment for {self.oa.oa_number} - {self.file.name}"