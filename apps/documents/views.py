# apps/documents/views.py

from django.http import Http404, StreamingHttpResponse
from django.template.loader import render_to_string
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from decimal import Decimal

from apps.quotations.models import Quotation
from apps.proforma.models import ProformaInvoice
from apps.accounts.models import TenantUser
from .models import TenantLetterhead
from .pdf_engine import generate_quotation_pdf, split_gst, amount_in_words


# ─────────────────────────────────────────────────────────────────────────────
# Context builder - Quotation
# ─────────────────────────────────────────────────────────────────────────────

def _build_quotation_context(quotation: Quotation) -> dict:
    """Assemble every variable the quotation.html template needs."""

    enquiry = quotation.enquiry
    customer = enquiry.customer
    tenant = quotation.tenant

    # ── Letterhead config (optional) ─────────────────────────────────────
    try:
        lh = tenant.letterhead
    except TenantLetterhead.DoesNotExist:
        lh = None

    # Accent colour: letterhead setting → tenant default
    accent_color = (lh.accent_color if lh else None) or '#122C41'

    # Company info: letterhead setting → tenant model
    company_name = (lh and lh.company_name) or getattr(tenant, 'company_name', '') or ''
    company_address = (lh and lh.company_address) or getattr(tenant, 'company_address', '') or ''
    company_phone = (lh and lh.company_phone) or ''
    company_email = (lh and lh.company_email) or ''
    company_gstin = (lh and lh.company_gstin) or getattr(tenant, 'gstin', '') or ''
    company_pan = (lh and lh.company_pan) or ''
    company_state = (lh and lh.company_state) or ''

    # ── Bank details ──────────────────────────────────────────────────────
    bank_name = (lh and lh.bank_name) or ''
    bank_account_name = (lh and lh.bank_account_name) or ''
    bank_branch = (lh and lh.bank_branch) or ''
    bank_account_number = (lh and lh.bank_account_number) or ''
    bank_ifsc = (lh and lh.bank_ifsc) or ''
    bank_micr = (lh and lh.bank_micr) or ''

    # ── Billing address ───────────────────────────────────────────────────
    billing_address = (
        customer.addresses.filter(address_type='BILLING', is_default=True).first()
        or customer.addresses.filter(address_type='BILLING').first()
    )

    # ── Line items ────────────────────────────────────────────────────────
    line_items_qs = quotation.line_items.all()
    line_items_ctx = []
    for item in line_items_qs:
        qty = Decimal(str(item.quantity or 0))
        price = Decimal(str(item.unit_price or 0))
        line_amount = qty * price
        line_items_ctx.append({
            'product_name_snapshot': item.product_name_snapshot,
            'description_snapshot': item.description_snapshot,
            'part_no': item.part_no,
            'customer_part_no': item.customer_part_no,
            'job_code': item.job_code,
            'hsn_snapshot': item.hsn_snapshot,
            'unit_snapshot': item.unit_snapshot,
            'quantity': item.quantity,
            'unit_price': item.unit_price,
            'line_amount': line_amount,
            'tax_percent': item.tax_percent,
            'tax_group_code': item.tax_group_code,
            'tax_amount': item.tax_amount,
            'line_total': item.line_total,
        })

    # ── GST split ─────────────────────────────────────────────────────────
    cgst, sgst, igst, cgst_rate, sgst_rate, igst_rate = split_gst(
        line_items_qs,
        customer_state=customer.state or '',
        company_state=company_state,
    )

    # ── Commercial terms ──────────────────────────────────────────────────
    try:
        terms = quotation.terms
    except Exception:
        terms = None

    # ── Date formatter ────────────────────────────────────────────────────
    def fmt_date(d):
        if not d:
            return '—'
        return d.strftime('%d %b %Y') if hasattr(d, 'strftime') else str(d)

    # ── Prepared by ───────────────────────────────────────────────────────
    prepared_by = ''
    if enquiry.assigned_to:
        prepared_by = (
            enquiry.assigned_to.get_full_name()
            or enquiry.assigned_to.username
        )

    return {
        # Branding
        'accent_color': accent_color,
        'is_draft': quotation.review_status != 'APPROVED',

        # Company / sender
        'company_name': company_name,
        'company_address': company_address,
        'company_phone': company_phone,
        'company_email': company_email,
        'company_gstin': company_gstin,
        'company_pan': company_pan,

        # Bank details
        'bank_name': bank_name,
        'bank_account_name': bank_account_name,
        'bank_branch': bank_branch,
        'bank_account_number': bank_account_number,
        'bank_ifsc': bank_ifsc,
        'bank_micr': bank_micr,

        # Document meta
        'quotation': quotation,
        'quotation_date': fmt_date(quotation.created_at),
        'valid_till_date': fmt_date(quotation.valid_till_date),

        # Enquiry
        'enquiry': enquiry,
        'enquiry_date': fmt_date(enquiry.enquiry_date),

        # Customer
        'customer': customer,
        'billing_address': billing_address,

        # Line items
        'line_items': line_items_ctx,

        # Tax split
        'cgst_amount': cgst,
        'sgst_amount': sgst,
        'igst_amount': igst,
        'cgst_rate': cgst_rate,
        'sgst_rate': sgst_rate,
        'igst_rate': igst_rate,

        # Terms
        'terms': terms,

        # Misc
        'prepared_by': prepared_by,
        'amount_in_words': amount_in_words(
            quotation.grand_total, quotation.currency
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Context builder - Proforma Invoice
# ─────────────────────────────────────────────────────────────────────────────

def _build_proforma_context(proforma: ProformaInvoice) -> dict:
    """Assemble every variable the proforma.html template needs."""

    order = proforma.order
    oa = order.oa if hasattr(order, 'oa') and order.oa else None
    quotation = oa.quotation if oa else None
    enquiry = quotation.enquiry if quotation else None
    customer = enquiry.customer if enquiry else None
    tenant = proforma.tenant

    # ── Letterhead config ─────────────────────────────────────────────────────
    try:
        lh = tenant.letterhead
    except TenantLetterhead.DoesNotExist:
        lh = None

    accent_color = (lh.accent_color if lh else None) or '#122C41'

    # Company info
    company_name = (lh and lh.company_name) or getattr(tenant, 'company_name', '') or ''
    company_address = (lh and lh.company_address) or getattr(tenant, 'company_address', '') or ''
    company_phone = (lh and lh.company_phone) or ''
    company_email = (lh and lh.company_email) or ''
    company_gstin = (lh and lh.company_gstin) or getattr(tenant, 'gstin', '') or ''
    company_pan = (lh and lh.company_pan) or ''
    company_state = (lh and lh.company_state) or ''

    # Bank details
    bank_name = (lh and lh.bank_name) or ''
    bank_account_name = (lh and lh.bank_account_name) or ''
    bank_branch = (lh and lh.bank_branch) or ''
    bank_account_number = (lh and lh.bank_account_number) or ''
    bank_ifsc = (lh and lh.bank_ifsc) or ''
    bank_micr = (lh and lh.bank_micr) or ''

    # Billing address
    billing_address = None
    if customer:
        billing_address = (
            customer.addresses.filter(address_type='BILLING', is_default=True).first()
            or customer.addresses.filter(address_type='BILLING').first()
        )

    # ── Line items ────────────────────────────────────────────────────────────
    line_items_qs = proforma.line_items.all()
    line_items_ctx = []
    sub_total = Decimal('0')
    total_tax = Decimal('0')

    for item in line_items_qs:
        qty = Decimal(str(item.quantity or 0))
        price = Decimal(str(item.unit_price or 0))
        amount = qty * price
        sub_total += amount
        total_tax += Decimal(str(item.tax_amount or 0))

        line_items_ctx.append({
            'job_code': item.job_code or '',
            'customer_part_no': item.customer_part_no or '',
            'part_no': item.part_no or '',
            'description': item.description or '',
            'hsn_code': item.hsn_code or '',
            'quantity': item.quantity,
            'unit': item.unit or 'NOS',
            'unit_price': item.unit_price,
            'amount': amount,
            'tax_percent': item.tax_percent,
            'tax_amount': item.tax_amount,
            'line_total': item.total,
        })

    # ── GST split ─────────────────────────────────────────────────────────────
    # Determine if intra-state or inter-state
    cust_state = (customer.state if customer else '').strip().lower() if customer else ''
    comp_state = (company_state or '').strip().lower()
    intra = bool(cust_state and comp_state and cust_state == comp_state)

    if intra:
        cgst = (total_tax / 2).quantize(Decimal('0.01'))
        sgst = (total_tax / 2).quantize(Decimal('0.01'))
        igst = Decimal('0')
        # Calculate effective rates
        eff_rate = ((total_tax / sub_total) * 100) if sub_total else Decimal('0')
        cgst_rate = (eff_rate / 2).quantize(Decimal('0.01'))
        sgst_rate = (eff_rate / 2).quantize(Decimal('0.01'))
        igst_rate = Decimal('0')
    else:
        cgst = Decimal('0')
        sgst = Decimal('0')
        igst = total_tax
        cgst_rate = Decimal('0')
        sgst_rate = Decimal('0')
        igst_rate = ((total_tax / sub_total) * 100) if sub_total else Decimal('0')
        igst_rate = igst_rate.quantize(Decimal('0.01'))

    # ── Prepared by ───────────────────────────────────────────────────────────
    prepared_by = ''
    if enquiry and enquiry.assigned_to:
        prepared_by = (
            enquiry.assigned_to.get_full_name()
            or enquiry.assigned_to.username
        )

    # ── Date formatter ────────────────────────────────────────────────────────
    def fmt_date(d):
        if not d:
            return '—'
        return d.strftime('%d %b %Y') if hasattr(d, 'strftime') else str(d)

    # ── Order references with safe fallbacks ──────────────────────────────────
    order_number = order.order_number if order else ''
    oa_number = oa.oa_number if oa else ''
    enquiry_number = enquiry.enquiry_number if enquiry else ''

    # ── Customer info with safe fallbacks ─────────────────────────────────────
    customer_name = customer.company_name if customer else ''
    customer_email = customer.email if customer else ''
    customer_phone = customer.telephone_primary if customer else ''
    customer_gstin = customer.gst_number if customer else ''

    return {
        # Branding
        'accent_color': accent_color,

        # Company / sender
        'company_name': company_name,
        'company_address': company_address,
        'company_phone': company_phone,
        'company_email': company_email,
        'company_gstin': company_gstin,
        'company_pan': company_pan,
        'company_state': company_state,

        # Bank details
        'bank_name': bank_name,
        'bank_account_name': bank_account_name,
        'bank_branch': bank_branch,
        'bank_account_number': bank_account_number,
        'bank_ifsc': bank_ifsc,
        'bank_micr': bank_micr,

        # Document info
        'proforma': proforma,
        'proforma_number': proforma.proforma_number,
        'invoice_date': fmt_date(proforma.invoice_date),
        'status': proforma.status,
        'currency': proforma.currency,

        # Order references
        'order_number': order_number,
        'oa_number': oa_number,
        'enquiry_number': enquiry_number,

        # Customer
        'customer_name': customer_name,
        'customer_email': customer_email,
        'customer_phone': customer_phone,
        'customer_gstin': customer_gstin,
        'billing_address': billing_address,

        # Line items
        'line_items': line_items_ctx,

        # Financials
        'sub_total': sub_total,
        'total_tax': total_tax,
        'total_amount': proforma.total_amount,
        'total_paid': proforma.total_paid,
        'total_receivable': proforma.total_receivable,

        # Deductions
        'ff_percentage': proforma.ff_percentage,
        'ff_amount': proforma.ff_amount,
        'discount_percentage': proforma.discount_percentage,
        'discount_amount': proforma.discount_amount,
        'advance_percentage': proforma.advance_percentage,
        'advance_amount': proforma.advance_amount,

        # GST split
        'cgst_amount': cgst,
        'sgst_amount': sgst,
        'igst_amount': igst,
        'cgst_rate': cgst_rate,
        'sgst_rate': sgst_rate,
        'igst_rate': igst_rate,

        # Misc
        'prepared_by': prepared_by,
        'amount_in_words': amount_in_words(proforma.total_receivable, proforma.currency),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Quotation PDF view
# ─────────────────────────────────────────────────────────────────────────────

class QuotationPDFView(APIView):
    """
    GET /api/documents/quotation/{id}/pdf/
        Returns the quotation as a PDF stamped onto the tenant's letterhead.

        ?download=true  → Content-Disposition: attachment  (browser download)
        (default)       → Content-Disposition: inline      (iframe preview)
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):

        # 1. Fetch
        try:
            quotation = (
                Quotation.objects
                .select_related(
                    'enquiry',
                    'enquiry__customer',
                    'enquiry__assigned_to',
                    'tenant',
                    'tenant__letterhead',
                )
                .prefetch_related(
                    'line_items',
                    'enquiry__customer__addresses',
                )
                .get(pk=pk, tenant=request.tenant)
            )
        except Quotation.DoesNotExist:
            raise Http404

        # 2 & 3. Context + render HTML
        context = _build_quotation_context(quotation)
        html = render_to_string('documents/quotation.html', context)

        # 4 & 5. Generate PDF
        try:
            lh = quotation.tenant.letterhead
            letterhead_file = lh.letterhead_pdf if lh.letterhead_pdf else None
        except TenantLetterhead.DoesNotExist:
            letterhead_file = None

        try:
            pdf_bytes = generate_quotation_pdf(
                html=html,
                base_url=request.build_absolute_uri('/'),
                letterhead_pdf_file=letterhead_file,
            )
        except ImportError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        except Exception as exc:
            return Response(
                {'error': f'PDF generation failed: {exc}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # 6. Return
        filename = f"Quotation_{quotation.quotation_number}.pdf"
        as_download = request.query_params.get('download', 'false').lower() == 'true'
        disposition = 'attachment' if as_download else 'inline'

        response = StreamingHttpResponse(
            streaming_content=iter([pdf_bytes]),
            content_type='application/pdf',
        )
        response['Content-Disposition'] = (
            f'{disposition}; filename="{filename}"; '
            f"filename*=UTF-8''{filename}"
        )
        response['Content-Length'] = len(pdf_bytes)
        response['X-Frame-Options'] = 'SAMEORIGIN'
        response['Cache-Control'] = 'private, no-transform'
        return response


# ─────────────────────────────────────────────────────────────────────────────
# Proforma PDF view
# ─────────────────────────────────────────────────────────────────────────────

class ProformaPDFView(APIView):
    """
    GET /api/documents/proforma/{id}/pdf/
        Returns the proforma invoice as a PDF stamped onto the tenant's letterhead.

        ?download=true  → Content-Disposition: attachment (browser download)
        (default)       → Content-Disposition: inline (iframe preview)
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        # 1. Fetch proforma with all related data
        try:
            proforma = (
                ProformaInvoice.objects
                .select_related(
                    'order',
                    'order__oa',
                    'order__oa__quotation',
                    'order__oa__quotation__enquiry',
                    'order__oa__quotation__enquiry__customer',
                    'tenant',
                    'tenant__letterhead',
                )
                .prefetch_related(
                    'line_items',
                    'order__oa__quotation__enquiry__customer__addresses',
                )
                .get(pk=pk, tenant=request.tenant)
            )
        except ProformaInvoice.DoesNotExist:
            raise Http404

        # 2. Check permissions (employees can only view proformas assigned to them)
        tenant_user = TenantUser.objects.filter(
            user=request.user,
            tenant=request.tenant
        ).first()

        if tenant_user and tenant_user.role == 'employee':
            # Navigate safely through relations
            try:
                assigned_to = proforma.order.oa.quotation.enquiry.assigned_to
                if assigned_to and assigned_to != request.user:
                    raise PermissionDenied("You can only view proformas assigned to you.")
            except AttributeError:
                # If any relation is missing, employee can't view
                raise PermissionDenied("You don't have permission to view this proforma.")

        # 3. Build context and render HTML
        context = _build_proforma_context(proforma)
        html = render_to_string('documents/proforma.html', context)

        # 4. Get letterhead PDF if available
        try:
            lh = proforma.tenant.letterhead
            letterhead_file = lh.letterhead_pdf if lh and lh.letterhead_pdf else None
        except TenantLetterhead.DoesNotExist:
            letterhead_file = None

        # 5. Generate PDF
        try:
            pdf_bytes = generate_quotation_pdf(
                html=html,
                base_url=request.build_absolute_uri('/'),
                letterhead_pdf_file=letterhead_file,
            )
        except ImportError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        except Exception as exc:
            return Response(
                {'error': f'PDF generation failed: {exc}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # 6. Return PDF
        filename = f"Proforma_{proforma.proforma_number}.pdf"
        as_download = request.query_params.get('download', 'false').lower() == 'true'
        disposition = 'attachment' if as_download else 'inline'

        response = StreamingHttpResponse(
            streaming_content=iter([pdf_bytes]),
            content_type='application/pdf',
        )
        response['Content-Disposition'] = (
            f'{disposition}; filename="{filename}"; '
            f"filename*=UTF-8''{filename}"
        )
        response['Content-Length'] = len(pdf_bytes)
        response['X-Frame-Options'] = 'SAMEORIGIN'
        response['Cache-Control'] = 'private, no-transform'
        return response


# ─────────────────────────────────────────────────────────────────────────────
# Letterhead upload / retrieve
# ─────────────────────────────────────────────────────────────────────────────

_SCALAR_FIELDS = [
    'company_name', 'company_address', 'company_phone',
    'company_email', 'company_gstin', 'company_pan',
    'company_state', 'accent_color',
    # Bank details
    'bank_name', 'bank_account_name', 'bank_branch',
    'bank_account_number', 'bank_ifsc', 'bank_micr',
]

_LAYOUT_GUIDE = {
    'top_mm': 45,
    'bottom_mm': 28,
    'left_mm': 18,
    'right_mm': 18,
    'note': (
        'Design your letterhead PDF so the header fits within '
        'the top 45 mm and the footer within the bottom 28 mm. '
        'Leave the middle area completely blank — quotation '
        'content is stamped there automatically.'
    ),
}


class LetterheadView(APIView):
    """
    GET  /api/documents/letterhead/   → current config for this tenant
    POST /api/documents/letterhead/   → upload blank letterhead PDF + company info + bank details

    POST fields (all optional — save only what is provided):
        letterhead_pdf          — the blank A4 PDF file
        company_name
        company_address
        company_phone
        company_email
        company_gstin
        company_pan
        company_state           — used for CGST/SGST vs IGST calculation
        accent_color            — hex, e.g. #122C41
        bank_name
        bank_account_name
        bank_branch
        bank_account_number
        bank_ifsc
        bank_micr

    Standard layout rule (tell your tenants):
        Top 45 mm    → their header / logo
        Bottom 28 mm → their footer
        Left 18 mm   → gutter
        Right 18 mm  → gutter
        Middle       → leave completely blank — quotation content goes here
    """

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def get(self, request):
        try:
            lh = request.tenant.letterhead
        except TenantLetterhead.DoesNotExist:
            return Response({
                'configured': False,
                'layout_guide': _LAYOUT_GUIDE,
            })

        return Response({
            'configured': True,
            'id': lh.id,
            # Company info
            'company_name': lh.company_name,
            'company_address': lh.company_address,
            'company_phone': lh.company_phone,
            'company_email': lh.company_email,
            'company_gstin': lh.company_gstin,
            'company_pan': lh.company_pan,
            'company_state': lh.company_state,
            'accent_color': lh.accent_color,
            # Bank details
            'bank_name': lh.bank_name,
            'bank_account_name': lh.bank_account_name,
            'bank_branch': lh.bank_branch,
            'bank_account_number': lh.bank_account_number,
            'bank_ifsc': lh.bank_ifsc,
            'bank_micr': lh.bank_micr,
            # Letterhead PDF
            'has_letterhead_pdf': bool(lh.letterhead_pdf),
            'letterhead_pdf_url': (
                request.build_absolute_uri(lh.letterhead_pdf.url)
                if lh.letterhead_pdf else None
            ),
            'layout_guide': _LAYOUT_GUIDE,
        })

    def post(self, request):
        try:
            lh = request.tenant.letterhead
        except TenantLetterhead.DoesNotExist:
            lh = TenantLetterhead(tenant=request.tenant)

        for field in _SCALAR_FIELDS:
            if field in request.data:
                setattr(lh, field, request.data[field])

        if 'letterhead_pdf' in request.FILES:
            lh.letterhead_pdf = request.FILES['letterhead_pdf']

        lh.save()
        return Response({
            'success': True,
            'message': 'Letterhead saved successfully.',
            'layout_reminder': (
                'Ensure your letterhead PDF keeps the header within '
                'the top 45 mm, footer within the bottom 28 mm, and '
                'leaves the middle completely blank.'
            ),
        })