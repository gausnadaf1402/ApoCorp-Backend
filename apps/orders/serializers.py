from rest_framework import serializers
from django.db import transaction
from django.utils import timezone
from core.mixins import CustomerLockValidationMixin
from apps.customers.serializers import CustomerReadSerializer
from .models import (
    OrderAcknowledgement,
    OALineItem,
    OACommercialTerms,
    Order
)


class OALineItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = OALineItem
        fields = '__all__'
        read_only_fields = ('oa',)


class OACommercialTermsSerializer(serializers.ModelSerializer):
    class Meta:
        model = OACommercialTerms
        exclude = ('oa',)

    # ── Payment milestones: [{option, percentage, days_after_invoice?, lc_usance_days?, label?}, ...] ──
    PAYMENT_OPTIONS = {
        'with_po',
        'drawing_approval',
        'proforma_invoice',
        'credit_days',
        'letter_of_credit',
        'custom',
    }

    def validate_payment_milestones(self, value):
        if value in (None, ''):
            return []
        if not isinstance(value, list):
            raise serializers.ValidationError("payment_milestones must be a list.")

        total_pct = 0
        for i, milestone in enumerate(value):
            if not isinstance(milestone, dict):
                raise serializers.ValidationError(
                    f"payment_milestones[{i}] must be an object."
                )
            option = milestone.get('option')
            if option not in self.PAYMENT_OPTIONS:
                raise serializers.ValidationError(
                    f"payment_milestones[{i}].option must be one of {sorted(self.PAYMENT_OPTIONS)}."
                )
            if option == 'custom' and not milestone.get('label'):
                raise serializers.ValidationError(
                    f"payment_milestones[{i}]: 'custom' option requires a 'label'."
                )
            if option == 'credit_days' and not milestone.get('days_after_invoice'):
                raise serializers.ValidationError(
                    f"payment_milestones[{i}]: 'credit_days' option requires 'days_after_invoice'."
                )
            if option == 'letter_of_credit' and not milestone.get('lc_usance_days'):
                raise serializers.ValidationError(
                    f"payment_milestones[{i}]: 'letter_of_credit' option requires 'lc_usance_days'."
                )
            pct = milestone.get('percentage') or 0
            try:
                total_pct += float(pct)
            except (TypeError, ValueError):
                raise serializers.ValidationError(
                    f"payment_milestones[{i}].percentage must be numeric."
                )

        # Soft check only — rounding across milestones is common, so we
        # warn via a wide tolerance rather than hard-blocking the save.
        if total_pct and (total_pct < 99 or total_pct > 101) and total_pct != 0:
            raise serializers.ValidationError(
                f"payment_milestones percentages sum to {total_pct}, expected ~100."
            )
        return value

    def validate_special_notes(self, value):
        if value in (None, ''):
            return []
        if not isinstance(value, list) or not all(isinstance(n, str) for n in value):
            raise serializers.ValidationError("special_notes must be a list of strings.")
        return value


class OrderAcknowledgementSerializer(
    CustomerLockValidationMixin,
    serializers.ModelSerializer
):
    line_items = OALineItemSerializer(many=True)
    commercial_terms = OACommercialTermsSerializer(required=False)

    # ── Live customer/enquiry data via FK chain ──
    customer_detail = CustomerReadSerializer(
        source='quotation.enquiry.customer', read_only=True
    )
    enquiry_number = serializers.CharField(
        source='quotation.enquiry.enquiry_number', read_only=True
    )
    quotation_number = serializers.CharField(
        source='quotation.quotation_number', read_only=True
    )
    assigned_to_id = serializers.IntegerField(
        source='quotation.enquiry.assigned_to.id', read_only=True
    )

    class Meta:
        model = OrderAcknowledgement
        fields = "__all__"
        read_only_fields = (
            "tenant",
            "total_value",   # Always recalculated server-side
            "currency",
            "exchange_rate",
            "cancelled_at",
            "oa_number",
            # NOTE: status is NOT read-only — frontend sends DRAFT when saving
        )

    def validate(self, attrs):
        quotation = attrs.get("quotation")
        if quotation and quotation.review_status != "APPROVED":
            raise serializers.ValidationError(
                "Quotation must be approved before creating OA."
            )
        if quotation and quotation.enquiry.customer:
            self.validate_customer_not_locked(quotation.enquiry.customer)
        return attrs

    def _calculate_totals(self, line_items_data):
        """
        Calculate sub_total (excl tax), total_tax, and grand_total
        from a list of line item dicts. Returns (sub_total, total_tax, grand_total).
        """
        sub_total = 0
        total_tax = 0
        for item in line_items_data:
            qty = float(item.get('quantity') or 0)
            price = float(item.get('unit_price') or 0)
            tax_pct = float(item.get('tax_percent') or 0)
            line_excl = qty * price
            line_tax = line_excl * (tax_pct / 100)
            sub_total += line_excl
            total_tax += line_tax
        return sub_total, total_tax, sub_total + total_tax

    def to_representation(self, instance):
        """Override to always show latest PO number from quotation"""
        data = super().to_representation(instance)

        # Override customer_po_number in transport_details with live quotation.po_number
        if instance.quotation and data.get('transport_details'):
            data['transport_details']['customer_po_number'] = instance.quotation.po_number or "NA"

        return data

    def _enrich_line_items(self, line_items_data):
        """
        Recalculate tax_amount and total on each line item dict in-place.
        Returns the enriched list.
        """
        enriched = []
        for item in line_items_data:
            item = dict(item)
            qty = float(item.get('quantity') or 0)
            price = float(item.get('unit_price') or 0)
            tax_pct = float(item.get('tax_percent') or 0)
            line_excl = qty * price
            line_tax = round(line_excl * (tax_pct / 100), 2)
            item['tax_amount'] = line_tax
            item['total'] = round(line_excl + line_tax, 2)
            enriched.append(item)
        return enriched

    @transaction.atomic
    def create(self, validated_data):
        line_items_data = validated_data.pop("line_items")
        commercial_terms_data = validated_data.pop("commercial_terms", None)

        quotation = validated_data["quotation"]

        # Copy currency/exchange_rate from quotation
        validated_data["currency"] = quotation.currency
        validated_data["exchange_rate"] = quotation.exchange_rate

        # Enrich line items and calculate total_value from them
        enriched_items = self._enrich_line_items(line_items_data)
        _, _, grand_total = self._calculate_totals(enriched_items)
        validated_data["total_value"] = grand_total

        # Status defaults to PENDING (new OA from Generate OA button)
        # Allow override if explicitly sent (e.g. DRAFT)
        if "status" not in validated_data:
            validated_data["status"] = "PENDING"

        # Let the model auto-generate oa_number
        validated_data.pop("oa_number", None)

        oa = OrderAcknowledgement.objects.create(**validated_data)

        for item in enriched_items:
            OALineItem.objects.create(oa=oa, **item)

        if commercial_terms_data:
            OACommercialTerms.objects.create(oa=oa, **commercial_terms_data)

        return oa

    @transaction.atomic
    def update(self, instance, validated_data):
        line_items_data = validated_data.pop("line_items", None)
        commercial_terms_data = validated_data.pop("commercial_terms", None)

        # Apply scalar field updates
        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        # Recalculate total_value if line items are being updated
        if line_items_data is not None:
            enriched_items = self._enrich_line_items(line_items_data)
            _, _, grand_total = self._calculate_totals(enriched_items)
            instance.total_value = grand_total

        instance.last_activity_at = timezone.now()
        instance.save()

        if line_items_data is not None:
            instance.line_items.all().delete()
            for item in enriched_items:
                OALineItem.objects.create(oa=instance, **item)

        if commercial_terms_data is not None:
            if hasattr(instance, 'commercial_terms'):
                instance.commercial_terms.delete()
            OACommercialTerms.objects.create(oa=instance, **commercial_terms_data)

        return instance


class OrderDetailSerializer(serializers.ModelSerializer):
    """Detailed Order serializer with full OA data for invoice creation"""

    # OA fields
    oa_number = serializers.CharField(source='oa.oa_number', read_only=True)
    oa_status = serializers.CharField(source='oa.status', read_only=True)
    oa_total_value = serializers.DecimalField(source='oa.total_value', read_only=True, max_digits=15, decimal_places=2)

    # OA Line Items (critical for invoice creation)
    oa_line_items = OALineItemSerializer(source='oa.line_items', many=True, read_only=True)

    # Address snapshots (bill_to / ship_to from OA)
    billing_snapshot = serializers.JSONField(source='oa.billing_snapshot', read_only=True)
    shipping_snapshot = serializers.JSONField(source='oa.shipping_snapshot', read_only=True)

    # Transport details (for pre-populating logistics step)
    transport_details = serializers.JSONField(source='oa.transport_details', read_only=True)

    # Live customer data
    customer_detail = CustomerReadSerializer(  # Import from apps.customers.serializers
        source='oa.quotation.enquiry.customer', read_only=True
    )
    enquiry_number = serializers.CharField(
        source='oa.quotation.enquiry.enquiry_number', read_only=True
    )
    quotation_number = serializers.CharField(
        source='oa.quotation.quotation_number', read_only=True
    )
    po_number = serializers.CharField(
        source='oa.quotation.po_number', read_only=True
    )

    # Commercial terms
    commercial_terms = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            'id', 'order_number', 'status', 'stage', 'order_category',
            'invoice_status', 'currency', 'exchange_rate', 'total_value',
            'advance_paid', 'created_at', 'tenant', 'oa',
            # Extra OA fields
            'oa_number', 'oa_status', 'oa_total_value',
            'oa_line_items',  # ← This is what frontend needs!
            'billing_snapshot',   # ← bill_to pre-fill
            'shipping_snapshot',  # ← ship_to pre-fill
            'transport_details',  # ← logistics pre-fill
            'customer_detail', 'enquiry_number', 'quotation_number',
            'po_number', 'commercial_terms',
        ]

    def get_commercial_terms(self, obj):
        if hasattr(obj.oa, 'commercial_terms'):
            from .serializers import OACommercialTermsSerializer
            return OACommercialTermsSerializer(obj.oa.commercial_terms).data
        return None


class OrderSerializer(serializers.ModelSerializer):
    """Basic Order serializer for list views"""

    oa_number = serializers.CharField(source='oa.oa_number', read_only=True)
    customer_detail = CustomerReadSerializer(
        source='oa.quotation.enquiry.customer', read_only=True
    )
    enquiry_number = serializers.CharField(
        source='oa.quotation.enquiry.enquiry_number', read_only=True
    )

    class Meta:
        model = Order
        fields = "__all__"
        read_only_fields = ("tenant",)