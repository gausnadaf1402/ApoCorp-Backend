from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied, ValidationError
from decimal import Decimal

from core.viewsets import TenantModelViewSet
from core.mixins import ModelPermissionMixin
from apps.accounts.models import TenantUser
from .models import ProformaInvoice
from .serializers import ProformaInvoiceSerializer, ProformaPaymentRecordSerializer


class ProformaInvoiceViewSet(ModelPermissionMixin, TenantModelViewSet):

    queryset = ProformaInvoice.objects.all()
    serializer_class = ProformaInvoiceSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()

        tenant_user = TenantUser.objects.filter(
            user=self.request.user,
            tenant=self.request.tenant
        ).first()

        if tenant_user and tenant_user.role == 'employee':
            queryset = queryset.filter(
                order__oa__quotation__enquiry__assigned_to=self.request.user
            )

        # ?status=DRAFT  or  ?status=SENT,PARTIAL,PAID
        status = self.request.query_params.get('status')
        if status:
            if ',' in status:
                queryset = queryset.filter(status__in=status.split(','))
            else:
                queryset = queryset.filter(status__iexact=status)

        # ?order=<uuid>
        order_id = self.request.query_params.get('order')
        if order_id:
            queryset = queryset.filter(order__id=order_id)

        return queryset

    def _check_employee_permission(self, instance):
        tenant_user = TenantUser.objects.filter(
            user=self.request.user,
            tenant=self.request.tenant
        ).first()
        if tenant_user and tenant_user.role == 'employee':
            if instance.order.oa.quotation.enquiry.assigned_to != self.request.user:
                raise PermissionDenied('You can only modify proforma invoices assigned to you.')

    def perform_update(self, serializer):
        self._check_employee_permission(self.get_object())
        serializer.save()

    # ── Initialize ────────────────────────────────────────────────────────────
    @action(detail=False, methods=['post'], url_path='initialize')
    def initialize(self, request):
        """
        POST /proforma/initialize/  { "order": "<uuid>" }
        Returns existing proforma if one already exists, otherwise creates a new DRAFT.
        """
        order_id = request.data.get('order')
        if not order_id:
            raise ValidationError({'order': 'This field is required.'})

        # Return existing proforma if already created
        try:
            existing = ProformaInvoice.objects.get(
                order__id=order_id,
                tenant=request.tenant
            )
            return Response({
                'id':       str(existing.id),
                'status':   existing.status,
                'proforma': self.get_serializer(existing).data,
            })
        except ProformaInvoice.DoesNotExist:
            pass

        # Load order
        from apps.orders.models import Order
        try:
            order = Order.objects.get(id=order_id, tenant=request.tenant)
        except Order.DoesNotExist:
            raise ValidationError({'order': 'Order not found.'})

        import datetime
        serializer = self.get_serializer(data={
            'order':        order.id,
            'invoice_date': str(datetime.date.today()),
        })
        serializer.is_valid(raise_exception=True)
        proforma = serializer.save(tenant=request.tenant)

        return Response({
            'id':       str(proforma.id),
            'status':   proforma.status,
            'proforma': self.get_serializer(proforma).data,
        })

    # ── Send ─────────────────────────────────────────────────────────────────
# Add this method to ProformaInvoiceViewSet class

    @action(detail=True, methods=['post'])
    def send(self, request, pk=None):
        """
        POST /proforma/{id}/send/
        Marks the proforma as SENT and returns PDF URL
        """
        proforma = self.get_object()
        self._check_employee_permission(proforma)

        if proforma.status != 'DRAFT':
            raise PermissionDenied('Only DRAFT proformas can be sent.')

        proforma.status = 'SENT'
        proforma.save(update_fields=['status'])

        # Return PDF URL for frontend to use
        pdf_url = f"/api/documents/proforma/{proforma.id}/pdf/?download=true"
        
        return Response({
            'message': 'Proforma invoice marked as sent.',
            'status': proforma.status,
            'pdf_url': pdf_url
        })

    # ── Update deductions ─────────────────────────────────────────────────────
    @action(detail=True, methods=['patch'], url_path='update_deductions')
    def update_deductions(self, request, pk=None):
        """
        PATCH /proforma/{id}/update_deductions/
        Body: { ff_percentage, discount_percentage, advance_percentage }
        Recalculates all amounts and total_receivable server-side.
        Allowed for any status except PAID.
        """
        proforma = self.get_object()
        self._check_employee_permission(proforma)

        if proforma.status == 'PAID':
            raise PermissionDenied('Cannot modify a fully paid invoice.')

        def get_pct(key):
            val = request.data.get(key)
            if val is None:
                return None
            try:
                pct = Decimal(str(val))
                if not (0 <= pct <= 100):
                    raise ValidationError({key: 'Must be between 0 and 100.'})
                return pct
            except Exception:
                raise ValidationError({key: 'Invalid number.'})

        ff_pct       = get_pct('ff_percentage')
        disc_pct     = get_pct('discount_percentage')
        advance_pct  = get_pct('advance_percentage')

        if ff_pct      is not None: proforma.ff_percentage       = ff_pct
        if disc_pct    is not None: proforma.discount_percentage = disc_pct
        if advance_pct is not None: proforma.advance_percentage  = advance_pct

        proforma.recalculate_deductions()
        proforma.save()

        return Response(self.get_serializer(proforma).data)

    # ── Add payment ───────────────────────────────────────────────────────────
    @action(detail=True, methods=['post'])
    def add_payment(self, request, pk=None):
        proforma = self.get_object()
        self._check_employee_permission(proforma)

        if proforma.status not in ('SENT', 'PARTIAL'):
            raise PermissionDenied('Payments can only be added to SENT or PARTIAL proformas.')

        serializer = ProformaPaymentRecordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        amount    = Decimal(str(request.data.get('amount', 0)))
        remaining = proforma.total_receivable

        if amount > remaining:
            raise ValidationError({
                'amount': f'Payment ₹{amount} exceeds remaining receivable ₹{remaining}.'
            })

        proforma.payments.create(
            payment_date     = request.data.get('payment_date'),
            amount           = amount,
            mode             = request.data.get('mode', 'NEFT'),
            reference_number = request.data.get('reference_number', ''),
        )

        # Recalculate paid totals and receivable
        proforma.recalculate_payments()

        # Update status
        proforma.status = 'PAID' if proforma.total_receivable <= 0 else 'PARTIAL'
        proforma.save()

        return Response({
            'message':    'Payment recorded successfully.',
            'total_paid': str(proforma.total_paid),
            'remaining':  str(proforma.total_receivable),
            'status':     proforma.status,
            'proforma':   self.get_serializer(proforma).data,
        })
    
    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        proforma = self.get_object()
        self._check_employee_permission(proforma)

        # ❌ Block if already paid
        if proforma.status == 'PAID':
            raise PermissionDenied('Cannot cancel a fully paid invoice.')

        # ❌ Optional: prevent duplicate cancel calls
        if proforma.status == 'CANCELLED':
            raise PermissionDenied('Proforma invoice is already cancelled.')

        # ✅ Allow cancel for all other states
        proforma.status = 'CANCELLED'
        proforma.save(update_fields=['status'])

        return Response({
            'message': 'Proforma invoice cancelled successfully.',
            'status': proforma.status
        })