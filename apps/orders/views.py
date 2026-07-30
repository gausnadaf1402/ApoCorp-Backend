from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from decimal import Decimal

from core.viewsets import TenantModelViewSet
from core.mixins import ModelPermissionMixin
from apps.accounts.models import TenantUser

from rest_framework.parsers import MultiPartParser, FormParser
from .models import OrderAcknowledgement, OALineItem, Order, OAAttachment
from .serializers import OrderAcknowledgementSerializer, OrderSerializer, OrderDetailSerializer, OAAttachmentSerializer


class OrderAcknowledgementViewSet(ModelPermissionMixin, TenantModelViewSet):

    queryset = OrderAcknowledgement.objects.all().order_by('-created_at')
    serializer_class = OrderAcknowledgementSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()

        tenant_user = TenantUser.objects.filter(
            user=self.request.user,
            tenant=self.request.tenant
        ).first()

        if tenant_user and tenant_user.role == 'employee':
            queryset = queryset.filter(
                quotation__enquiry__assigned_to=self.request.user
            )

        oa_number = self.request.query_params.get("oa_number")
        if oa_number:
            queryset = queryset.filter(oa_number=oa_number)

        status = self.request.query_params.get("status")
        if status:
            queryset = queryset.filter(status__iexact=status)

        quotation_id = self.request.query_params.get("quotation")
        if quotation_id:
            queryset = queryset.filter(quotation__id=quotation_id)

        return queryset

    def perform_update(self, serializer):
        instance = self.get_object()
        tenant_user = TenantUser.objects.filter(
            user=self.request.user,
            tenant=self.request.tenant
        ).first()

        if tenant_user and tenant_user.role == 'employee':
            if instance.quotation.enquiry.assigned_to != self.request.user:
                raise PermissionDenied("You can only update OAs assigned to you.")

        serializer.save()

    @action(detail=False, methods=['post'], url_path='initialize')
    @transaction.atomic
    def initialize(self, request):
        """
        Called when user clicks Generate OA on a quotation.
        - If OA already exists for this quotation, return it.
        - Otherwise create a new PENDING OA pre-filled from quotation data.
        Returns: { id, status, oa }
        """
        quotation_id = request.data.get("quotation")
        if not quotation_id:
            raise ValidationError({"quotation": "This field is required."})

        # Return existing OA if already created for this quotation
        try:
            existing = OrderAcknowledgement.objects.get(
                quotation__id=quotation_id,
                tenant=request.tenant
            )
            serializer = self.get_serializer(existing)
            return Response({
                "id":     str(existing.id),
                "status": existing.status,
                "oa":     serializer.data,
            })
        except OrderAcknowledgement.DoesNotExist:
            pass

        # Load quotation
        from apps.quotations.models import Quotation
        try:
            quotation = Quotation.objects.get(id=quotation_id, tenant=request.tenant)
        except Quotation.DoesNotExist:
            raise ValidationError({"quotation": "Quotation not found."})

        if quotation.review_status != "APPROVED":
            raise ValidationError({"quotation": "Quotation must be approved before creating OA."})

        # ── Build line items ──────────────────────────────────────────────────
        # QuotationLineItem snapshot fields:
        #   product_name_snapshot, description_snapshot, hsn_snapshot, unit_snapshot
        # Other fields: job_code, customer_part_no, part_no, quantity,
        #               unit_price, tax_percent, tax_group_code, line_total, tax_amount
        line_items_to_create = []
        sub_total = 0.0
        total_tax = 0.0

        for li in quotation.line_items.all():
            qty       = float(li.quantity)
            price     = float(li.unit_price)
            tax_pct   = float(li.tax_percent) if li.tax_percent else 0.0
            line_excl = qty * price
            line_tax  = round(line_excl * (tax_pct / 100), 2)
            line_tot  = round(line_excl + line_tax, 2)

            sub_total += line_excl
            total_tax += line_tax

            line_items_to_create.append({
                "job_code":         li.job_code         or "",
                "customer_part_no": li.customer_part_no or "",
                "part_no":          li.part_no          or "",
                "description":      li.product_name_snapshot or li.description_snapshot or "",
                "hsn_code":         li.hsn_snapshot      or "",
                "quantity":         li.quantity,
                "unit":             li.unit_snapshot      or "NOS",
                "unit_price":       li.unit_price,
                "tax_group_code":   li.tax_group_code    or "GST 18%",
                "tax_percent":      tax_pct,
                "tax_amount":       line_tax,
                "total":            line_tot,
            })

        grand_total = round(sub_total + total_tax, 2)

        # ── Billing / shipping snapshots ──────────────────────────────────────
        customer      = quotation.enquiry.customer
        billing_addr  = customer.addresses.filter(address_type='BILLING').first()
        shipping_addr = customer.addresses.filter(address_type='SHIPPING').first()

        billing_snapshot = {
            "entity_name":    billing_addr.entity_name    if billing_addr else customer.company_name,
            "address_line":   billing_addr.address_line   if billing_addr else "",
            "contact_person": billing_addr.contact_person if billing_addr else "",
            "contact_email":  billing_addr.contact_email  if billing_addr else (customer.email or ""),
            "contact_number": billing_addr.contact_number if billing_addr else (customer.telephone_primary or ""),
        }

        shipping_snapshot = {
            "entity_name":    shipping_addr.entity_name    if shipping_addr else customer.company_name,
            "address_line":   shipping_addr.address_line   if shipping_addr else "",
            "contact_person": shipping_addr.contact_person if shipping_addr else "",
            "contact_email":  shipping_addr.contact_email  if shipping_addr else (customer.email or ""),
            "contact_number": shipping_addr.contact_number if shipping_addr else (customer.telephone_primary or ""),
        }

        # ── Transport details ─────────────────────────────────────────────────
        order_num = f"OD{quotation.quotation_number.replace('QT', '')}"

        transport_details = {
            "order_number":          order_num,
            "order_book_number":     order_num,
            "order_type":            "std.mfg.comp",
            "order_date":            "",
            "quote_date":            str(quotation.created_at.date()) if quotation.created_at else "",
            "customer_po_number":    quotation.po_number or "NA",
            "po_date":               "",
            "delivery_date":         "",
            "division":              "LQP",
            "project_type":          "",
            "mode_of_transport":     "By Road",
            "preferred_transporter": "Will be intimated later",
            "packing_type":          "Card Board",
            "ecc_exemption":         "Not Applicable",
            "road_permit":           "Not Required",
            "shipping_gst":          "Not Required",
            "loi_number":            "NA",
            "project_name":          "NA",
        }

        # ── Create OA and line items ──────────────────────────────────────────
        oa = OrderAcknowledgement.objects.create(
            tenant=request.tenant,
            quotation=quotation,
            status='PENDING',
            billing_snapshot=billing_snapshot,
            shipping_snapshot=shipping_snapshot,
            transport_details=transport_details,
            currency=quotation.currency or "INR",
            exchange_rate=quotation.exchange_rate or 1,
            total_value=grand_total,
        )

        for item in line_items_to_create:
            OALineItem.objects.create(oa=oa, **item)

        serializer = self.get_serializer(oa)
        return Response({
            "id":     str(oa.id),
            "status": oa.status,
            "oa":     serializer.data,
        })

    @action(detail=True, methods=['post'])
    @transaction.atomic
    def share(self, request, pk=None):
        """
        Converts OA to CONVERTED and creates an Order record.
        Accepts PENDING or DRAFT status.
        """
        oa = self.get_object()

        if oa.status not in ('PENDING', 'DRAFT'):
            raise PermissionDenied(
                f"Cannot share OA with status '{oa.status}'. "
                "Only PENDING or DRAFT OAs can be shared."
            )

        if hasattr(oa, 'order'):
            raise PermissionDenied("Order already exists for this OA.")

        order = Order.objects.create(
            tenant=request.tenant,
            order_number=f"ORD-{oa.oa_number}",
            oa=oa,
            currency=oa.currency,
            exchange_rate=oa.exchange_rate,
            total_value=oa.total_value,
        )

        oa.status = 'CONVERTED'
        oa.last_activity_at = timezone.now()
        oa.save(update_fields=["status", "last_activity_at"])

        return Response({
            "message":      "OA shared and Order created successfully.",
            "order_id":     str(order.id),
            "order_number": order.order_number,
        })

    @action(detail=True, methods=["post"], parser_classes=[MultiPartParser, FormParser])
    def upload_file(self, request, pk=None):
        oa = self.get_object()
        file_obj = request.FILES.get("file")
        if not file_obj:
            return Response({"error": "No file provided"}, status=400)

        attachment = OAAttachment.objects.create(oa=oa, file=file_obj)
        oa.last_activity_at = timezone.now()
        oa.save(update_fields=['last_activity_at'])

        return Response(OAAttachmentSerializer(attachment, context={"request": request}).data)


class OrderViewSet(ModelPermissionMixin, TenantModelViewSet):

    queryset = Order.objects.all()
    serializer_class = OrderSerializer
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        """Use OrderDetailSerializer for retrieve action"""
        if self.action == 'retrieve':
            return OrderDetailSerializer
        return OrderSerializer

    def get_queryset(self):
        queryset = super().get_queryset()

        tenant_user = TenantUser.objects.filter(
            user=self.request.user,
            tenant=self.request.tenant
        ).first()

        if tenant_user and tenant_user.role == 'employee':
            return queryset.filter(
                oa__quotation__enquiry__assigned_to=self.request.user
            )

        return queryset

    @action(detail=True, methods=['get'])
    def dispatch_summary(self, request, pk=None):
        """
        Get detailed shipping status for an order.
        Shows per-line-item: shipped, remaining, and dispatch history.
        
        Clean architecture: Only goes through BackOrder → BackOrderLineItem → OALineItem
        No direct invoice line items considered (since all invoices come from BackOrders)
        """
        order = self.get_object()
        oa = order.oa
        
        if not oa:
            return Response({
                'error': 'No Order Acknowledgement linked to this order'
            }, status=400)
        
        # Calculate shipped quantities - ONLY through BackOrder (clean path)
        shipped_by_line = {}
        
        # Only consider confirmed/shipped backorders
        for backorder in order.back_orders.filter(
            status__in=['INVOICED', 'IN_TRANSIT', 'DELIVERED', 'COMPLETED']
        ):
            for item in backorder.line_items.select_related('oa_line_item').all():
                line_id = str(item.oa_line_item.id)
                shipped_by_line[line_id] = shipped_by_line.get(line_id, Decimal('0')) + item.quantity_dispatching
        
        # Build response
        line_items_summary = []
        total_shipped = Decimal('0')
        total_quantity = Decimal('0')
        
        for oa_line in oa.line_items.all():
            line_id = str(oa_line.id)
            total_qty = oa_line.quantity
            shipped_qty = shipped_by_line.get(line_id, Decimal('0'))
            remaining_qty = max(Decimal('0'), total_qty - shipped_qty)
            
            total_quantity += total_qty
            total_shipped += shipped_qty
            
            # Get dispatch history for this line item
            dispatches = []
            for backorder in order.back_orders.filter(
                line_items__oa_line_item=oa_line
            ).distinct().select_related('invoice'):
                try:
                    bo_item = backorder.line_items.get(oa_line_item=oa_line)
                    dispatches.append({
                        'back_order_number': backorder.back_order_number,
                        'quantity': float(bo_item.quantity_dispatching),
                        'status': backorder.status,
                        'invoice_number': backorder.invoice.invoice_number if backorder.invoice else None,
                        'created_at': backorder.created_at.isoformat() if backorder.created_at else None
                    })
                except Exception:
                    # Should not happen, but just in case
                    pass
            
            line_items_summary.append({
                'oa_line_item_id': line_id,
                'description': oa_line.description or oa_line.product_name_snapshot or '',
                'part_no': oa_line.part_no or '',
                'total_quantity': float(total_qty),
                'shipped_quantity': float(shipped_qty),
                'remaining_quantity': float(remaining_qty),
                'unit': oa_line.unit or 'NOS',
                'unit_price': float(oa_line.unit_price),
                'dispatches': dispatches
            })
        
        # Calculate invoice status dynamically
        if total_shipped == 0:
            invoice_status = 'NOT_INVOICED'
        elif total_shipped >= total_quantity:
            invoice_status = 'FULLY_INVOICED'
        else:
            invoice_status = 'PARTIALLY_INVOICED'
        
        total_qty_float = float(total_quantity)
        total_shipped_float = float(total_shipped)
        
        return Response({
            'order_id': str(order.id),
            'order_number': order.order_number,
            'order_category': order.order_category,
            'invoice_status': invoice_status,
            'total_quantity': total_qty_float,
            'shipped_quantity': total_shipped_float,
            'completion_percentage': round((total_shipped_float / total_qty_float * 100), 2) if total_qty_float > 0 else 0,
            'line_items': line_items_summary
        })


from rest_framework.viewsets import ModelViewSet

class OAAttachmentViewSet(ModelViewSet):
    queryset = OAAttachment.objects.all()
    serializer_class = OAAttachmentSerializer
    permission_classes = [IsAuthenticated]