# apps/quotations/serializers.py

from rest_framework import serializers
from django.utils import timezone
from apps.customers.serializers import CustomerReadSerializer
from apps.enquiries.serializers import EnquirySerializer
from apps.products.models import Product
from .models import (
    Quotation, QuotationLineItem,
    QuotationTerms, QuotationFollowUp,
    QuotationAttachment
)
from core.mixins import CustomerLockValidationMixin


class QuotationLineItemSerializer(serializers.ModelSerializer):

    product = serializers.UUIDField(required=False)

    class Meta:
        model = QuotationLineItem
        exclude = ("quotation",)
        read_only_fields = ("line_total", "tax_amount")


class QuotationTermsSerializer(serializers.ModelSerializer):

    class Meta:
        model = QuotationTerms
        exclude = ("quotation",)

    # ── Payment milestones: [{option, percentage, days_after_invoice?, lc_usance_days?, label?}, ...] ──
    # Same accepted shape as apps.orders.serializers.OACommercialTermsSerializer,
    # since the OA is generated from this quotation and both need to accept
    # identical frontend payloads for this clause.
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


class QuotationFollowUpSerializer(serializers.ModelSerializer):

    class Meta:
        model = QuotationFollowUp
        exclude = ("quotation",)
        read_only_fields = ("created_at",)


class QuotationAttachmentSerializer(serializers.ModelSerializer):

    class Meta:
        model = QuotationAttachment
        fields = "__all__"


class QuotationSerializer(CustomerLockValidationMixin, serializers.ModelSerializer):

    line_items = QuotationLineItemSerializer(many=True)
    terms = QuotationTermsSerializer(required=False)
    follow_ups = QuotationFollowUpSerializer(many=True, required=False)
    attachments = QuotationAttachmentSerializer(many=True, read_only=True)

    # Enquiry fields
    enquiry_number = serializers.CharField(source='enquiry.enquiry_number', read_only=True)
    enquiry_priority = serializers.CharField(source='enquiry.priority', read_only=True)
    enquiry_status = serializers.CharField(source='enquiry.status', read_only=True)

    # Additional enquiry fields for Requirement Details section
    enquiry_subject = serializers.CharField(source='enquiry.subject', read_only=True, default='')
    enquiry_product_name = serializers.CharField(source='enquiry.product_name', read_only=True, default='')
    enquiry_type = serializers.CharField(source='enquiry.enquiry_type', read_only=True, default='')
    enquiry_source = serializers.CharField(source='enquiry.source_of_enquiry', read_only=True, default='')
    enquiry_due_date = serializers.DateField(source='enquiry.due_date', read_only=True)
    enquiry_target_date = serializers.DateField(source='enquiry.target_submission_date', read_only=True)
    enquiry_prospective_value = serializers.DecimalField(source='enquiry.prospective_value', max_digits=15, decimal_places=2, read_only=True, default=None)
    enquiry_currency = serializers.CharField(source='enquiry.currency', read_only=True, default='INR')
    enquiry_region = serializers.CharField(source='enquiry.region', read_only=True, default='')
    enquiry_created_by = serializers.CharField(source='enquiry.created_by.username', read_only=True, default='')
    enquiry_created_at = serializers.DateTimeField(source='enquiry.created_at', read_only=True)

    assigned_to_id = serializers.IntegerField(source='enquiry.assigned_to.id', read_only=True)
    assigned_to_name = serializers.SerializerMethodField()
    regional_manager_name = serializers.SerializerMethodField()

    customer_detail = CustomerReadSerializer(source='enquiry.customer', read_only=True)

    class Meta:
        model = Quotation
        fields = "__all__"

        read_only_fields = (
            "tenant",
            "review_status",
            "visibility",
            "client_status",
            "total_amount",
            "tax_amount",
            "grand_total",
            "quotation_number",
        )

    def get_assigned_to_name(self, obj):
        user = obj.enquiry.assigned_to if obj.enquiry else None
        if user:
            return user.get_full_name() or user.username
        return None

    def get_regional_manager_name(self, obj):
        user = obj.enquiry.regional_manager if obj.enquiry else None
        if user:
            return user.get_full_name() or user.username
        return None

    def validate(self, attrs):

        enquiry = attrs.get("enquiry") or getattr(self.instance, "enquiry", None)

        if enquiry and enquiry.customer:
            self.validate_customer_not_locked(enquiry.customer)

        if self.instance is None and Quotation.objects.filter(enquiry=enquiry).exists():
            raise serializers.ValidationError(
                "A quotation already exists for this enquiry."
            )

        return attrs

    def create(self, validated_data):

        line_items_data = validated_data.pop("line_items")
        terms_data = validated_data.pop("terms", None)
        followups_data = validated_data.pop("follow_ups", [])

        quotation = Quotation.objects.create(**validated_data)

        total = 0
        total_tax = 0

        for item in line_items_data:

            product = None
            product_id = item.get("product")

            if product_id:
                try:
                    product = Product.objects.get(id=product_id)
                except Product.DoesNotExist:
                    product = None

            if product:
                item["product_name_snapshot"] = product.name
                item["description_snapshot"] = product.description
                item["hsn_snapshot"] = product.hsn_code
                item["unit_snapshot"] = product.unit.symbol if product.unit else ""

                if not item.get("unit_price"):
                    item["unit_price"] = product.default_sale_price or 0

            line_total = item["quantity"] * item["unit_price"]
            tax_amount = (line_total * item.get("tax_percent", 0)) / 100

            total += line_total
            total_tax += tax_amount

            QuotationLineItem.objects.create(
                quotation=quotation,
                line_total=line_total,
                tax_amount=tax_amount,
                **item
            )

        if terms_data:
            QuotationTerms.objects.create(quotation=quotation, **terms_data)

        for fu in followups_data:
            # Don't include follow_up_by in create - set it from request if needed
            fu.pop('follow_up_by', None)
            QuotationFollowUp.objects.create(quotation=quotation, **fu)

        quotation.total_amount = total
        quotation.tax_amount = total_tax
        quotation.grand_total = total + total_tax
        quotation.save()

        quotation.enquiry.status = "NEGOTIATION"
        quotation.enquiry.save(update_fields=["status"])

        return quotation

    def update(self, instance, validated_data):

        line_items_data = validated_data.pop("line_items", None)
        terms_data = validated_data.pop("terms", None)
        followups_data = validated_data.pop("follow_ups", None)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        instance.save()

        if line_items_data is not None:

            instance.line_items.all().delete()

            total = 0
            total_tax = 0

            for item in line_items_data:

                product = None
                product_id = item.get("product")

                if product_id:
                    try:
                        product = Product.objects.get(id=product_id)
                    except Product.DoesNotExist:
                        product = None

                if product:
                    item["product_name_snapshot"] = product.name
                    item["description_snapshot"] = product.description
                    item["hsn_snapshot"] = product.hsn_code
                    item["unit_snapshot"] = product.unit.symbol if product.unit else ""

                    if not item.get("unit_price"):
                        item["unit_price"] = product.default_sale_price or 0

                line_total = item["quantity"] * item["unit_price"]
                tax_amount = (line_total * item.get("tax_percent", 0)) / 100

                total += line_total
                total_tax += tax_amount

                QuotationLineItem.objects.create(
                    quotation=instance,
                    line_total=line_total,
                    tax_amount=tax_amount,
                    **item
                )

            instance.total_amount = total
            instance.tax_amount = total_tax
            instance.grand_total = total + total_tax
            instance.save()

        if terms_data is not None:

            if hasattr(instance, "terms"):
                instance.terms.delete()

            QuotationTerms.objects.create(quotation=instance, **terms_data)

        if followups_data is not None:

            instance.follow_ups.all().delete()

            for fu in followups_data:
                fu.pop('follow_up_by', None)
                QuotationFollowUp.objects.create(quotation=instance, **fu)

        return instance