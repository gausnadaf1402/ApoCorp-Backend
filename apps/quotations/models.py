import uuid
from django.db import models, transaction
from django.contrib.auth.models import User
from core.mixins import TenantModelMixin
from apps.enquiries.models import Enquiry


class Quotation(TenantModelMixin):

    REVIEW_STATUS = [
        ('UNDER_REVIEW', 'Under Review'),
        ('APPROVED', 'Approved'),
        ('REJECTED', 'Rejected'),
    ]

    VISIBILITY = [
        ('INTERNAL', 'Internal'),
        ('EXTERNAL', 'External'),
    ]

    CLIENT_STATUS = [
        ('DRAFT', 'Draft'),
        ('SENT', 'Sent'),
        ('UNDER_NEGOTIATION', 'Under Negotiation'),
        ('ACCEPTED', 'Accepted'),
        ('REJECTED_BY_CLIENT', 'Rejected By Client'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    quotation_number = models.CharField(max_length=50, unique=True, blank=True)

    # ── Live FK. Customer/enquiry info is read via enquiry.customer ──
    enquiry = models.OneToOneField(
        Enquiry, on_delete=models.CASCADE, related_name="quotation"
    )

    po_number = models.CharField(max_length=100, blank=True)
    valid_till_date = models.DateField(null=True, blank=True)
    expires_at = models.DateField(null=True, blank=True)

    review_status = models.CharField(max_length=20, choices=REVIEW_STATUS, default='UNDER_REVIEW')
    visibility = models.CharField(max_length=20, choices=VISIBILITY, default='INTERNAL')
    client_status = models.CharField(max_length=30, choices=CLIENT_STATUS, default='DRAFT')

    manager_remark = models.TextField(blank=True)
    rejected_at = models.DateTimeField(null=True, blank=True)

    currency = models.CharField(max_length=10)
    exchange_rate = models.DecimalField(max_digits=10, decimal_places=4, default=1)

    total_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    grand_total = models.DecimalField(max_digits=15, decimal_places=2, default=0)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self.quotation_number:
            with transaction.atomic():
                last = (
                    Quotation.objects
                    .select_for_update()
                    .order_by('-created_at')
                    .first()
                )
                number = 1 if not last else int(last.quotation_number[2:6]) + 1
                self.quotation_number = f"QT{number:04d}IND"
        super().save(*args, **kwargs)

    def __str__(self):
        return self.quotation_number

class QuotationLineItem(models.Model):

    quotation = models.ForeignKey(
        Quotation, on_delete=models.CASCADE, related_name="line_items"
    )

    product = models.ForeignKey(
        "products.Product",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="quotation_items"
    )

    job_code = models.CharField(max_length=100, blank=True)
    customer_part_no = models.CharField(max_length=100, blank=True)
    part_no = models.CharField(max_length=100, blank=True)

    product_name_snapshot = models.CharField(max_length=255)
    description_snapshot = models.TextField(blank=True)
    hsn_snapshot = models.CharField(max_length=50, blank=True)
    unit_snapshot = models.CharField(max_length=50, blank=True)

    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    unit_price = models.DecimalField(max_digits=15, decimal_places=2)

    tax_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    tax_group_code = models.CharField(max_length=50, blank=True)

    tax_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    line_total = models.DecimalField(max_digits=15, decimal_places=2)

    def __str__(self):
        return f"{self.product_name_snapshot} - {self.quotation.quotation_number}"


class QuotationTerms(models.Model):
    """
    Mirrors the "Commercial Terms & Conditions" printed form — same
    clause-by-clause structure as apps.orders.models.OACommercialTerms,
    since the OA is generated from the quotation and both need to accept
    the same frontend dropdown payloads. See OACommercialTerms for the
    per-clause option notes; kept brief here to avoid duplicating them.
    """

    quotation = models.OneToOneField(
        Quotation, on_delete=models.CASCADE, related_name="terms"
    )

    # ── 1) Price Basis ──
    price_basis = models.CharField(max_length=255, blank=True)

    # ── 2) Packing & Forwarding ──
    packing_forwarding = models.CharField(max_length=255, blank=True)
    packing_extra_percentage = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )

    # ── 3) Freight ──
    freight = models.CharField(max_length=255, blank=True)

    # ── 4) Insurance ──
    insurance = models.CharField(max_length=255, blank=True)

    # ── 5) GST ── (kept sales_tax/excise_duty below for any legacy use;
    # gst_terms is the clause actually on the current form)
    gst_terms = models.CharField(max_length=255, blank=True)
    sales_tax = models.CharField(max_length=100, blank=True)
    excise_duty = models.CharField(max_length=100, blank=True)

    # ── 6) Drawing Approval / Manufacturing Clearance ──
    drawing_approval = models.CharField(max_length=255, blank=True)

    # ── 7) Delivery ──
    # "delivery" kept as the free-text summary/fallback (existing field);
    # the two below hold the structured weeks + trigger event.
    delivery = models.TextField(blank=True)
    delivery_period_weeks = models.PositiveIntegerField(null=True, blank=True)
    delivery_basis = models.CharField(max_length=255, blank=True)

    # ── 8) Inspection & Dispatch Clearance ──
    inspection = models.CharField(max_length=255, blank=True)
    dispatch_clearance = models.CharField(max_length=255, blank=True)
    test_certificate = models.CharField(max_length=100, blank=True)

    # ── 9) Warranty ──
    warranty = models.TextField(blank=True)

    # ── 10) Terms of Payment ──
    # payment_terms kept as free-text summary/fallback (existing field);
    # payment_milestones holds the structured, possibly-multiple splits.
    # See OACommercialTermsSerializer.validate_payment_milestones for the
    # accepted shape — identical rules apply here.
    payment_terms = models.TextField(blank=True)
    payment_milestones = models.JSONField(default=list, blank=True)
    advance_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    days_after_invoicing = models.PositiveIntegerField(default=0)

    # ── 11) Technical Specifications & Bill of Material ──
    technical_spec_note = models.TextField(blank=True)

    # ── 12) Erection Guidelines ──
    erection_guidelines = models.CharField(max_length=255, blank=True)

    # ── 13) Commissioning Assistance ──
    commissioning_support = models.TextField(blank=True)
    commissioning_visits = models.PositiveIntegerField(null=True, blank=True)
    commissioning_days = models.PositiveIntegerField(null=True, blank=True)

    # ── Special Notes ──
    special_notes = models.JSONField(default=list, blank=True)
    directors_indemnity_percentage = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    pbg_percentage = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    abg_format = models.CharField(max_length=100, blank=True)
    pbg_format = models.CharField(max_length=100, blank=True)
    sd_format = models.CharField(max_length=100, blank=True)
    ld_clause = models.CharField(max_length=100, blank=True)

    # ── 15-21 Extra Commercial Terms ──
    price_validity = models.CharField(max_length=255, blank=True)
    po_cancellation = models.CharField(max_length=255, blank=True)
    ordering_info = models.TextField(blank=True)
    force_majeure = models.TextField(blank=True)
    cost_over_run = models.CharField(max_length=255, blank=True)
    late_delivery_charges = models.CharField(max_length=255, blank=True)
    country_origin_material_certs = models.CharField(max_length=255, blank=True)

    # ── Existing quotation-stage-only fields (unchanged) ──
    validity = models.TextField(blank=True)
    decision_expected = models.CharField(max_length=100, blank=True)
    remarks = models.TextField(blank=True)


class QuotationFollowUp(models.Model):

    quotation = models.ForeignKey(
        Quotation, on_delete=models.CASCADE, related_name="follow_ups"
    )

    follow_up_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    follow_up_date = models.DateField()
    contact_person = models.CharField(max_length=255, blank=True)
    contact_phone = models.CharField(max_length=20, blank=True)
    contact_email = models.EmailField(blank=True)
    remarks = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class QuotationAttachment(models.Model):
    quotation = models.ForeignKey(
        Quotation, on_delete=models.CASCADE, related_name="attachments"
    )
    file = models.FileField(upload_to="quotation_files/")
    uploaded_at = models.DateTimeField(auto_now_add=True)