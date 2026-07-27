from rest_framework import serializers
from .models import Product, ProductCategory, ProductType, UnitOfMeasure


class ProductCategorySerializer(serializers.ModelSerializer):
    """Serializer for Product Category (now a 3-level tree: L1 -> L2 -> L3)"""

    parent_name = serializers.CharField(source='parent.name', read_only=True)
    children_count = serializers.SerializerMethodField()

    class Meta:
        model = ProductCategory
        fields = [
            'id', 'name', 'code', 'parent', 'parent_name',
            'description', 'is_active', 'created_at', 'children_count'
        ]
        read_only_fields = ['id', 'created_at']

    def get_children_count(self, obj):
        return obj.children.filter(is_active=True).count()


class ProductTypeSerializer(serializers.ModelSerializer):
    """Serializer for Product Type"""
    
    class Meta:
        model = ProductType
        fields = ['id', 'code', 'name', 'description', 'is_active', 'created_at']
        read_only_fields = ['id', 'created_at']


class UnitOfMeasureSerializer(serializers.ModelSerializer):
    """Serializer for Unit of Measure"""
    
    class Meta:
        model = UnitOfMeasure
        fields = ['id', 'name', 'symbol', 'description', 'is_active', 'created_at']
        read_only_fields = ['id', 'created_at']


class ProductListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for list view"""
    
    category_name = serializers.CharField(source='category.name', read_only=True)
    unit_symbol = serializers.CharField(source='unit.symbol', read_only=True)
    product_type_name = serializers.CharField(source='product_type.name', read_only=True)
    
    class Meta:
        model = Product
        fields = [
            'id', 'part_no', 'legacy_part_no', 'name', 'category_name', 'unit_symbol',
            'product_type_name', 'default_sale_price', 'is_active',
            'is_locked', 'created_at'
        ]


class ProductSerializer(serializers.ModelSerializer):
    """Detailed serializer for create/update/retrieve"""
    
    # Read-only fields for nested data
    category_name = serializers.CharField(source='category.name', read_only=True)
    category_code = serializers.CharField(source='category.code', read_only=True)
    unit_name = serializers.CharField(source='unit.name', read_only=True)
    unit_symbol = serializers.CharField(source='unit.symbol', read_only=True)
    product_type_name = serializers.CharField(source='product_type.name', read_only=True)
    product_type_code = serializers.CharField(source='product_type.code', read_only=True)
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)

    # L1/L2/L3 breadcrumb, since category is stored as the L3 leaf node
    category_path = serializers.SerializerMethodField()

    # Write-only fields for foreign keys
    category_id = serializers.PrimaryKeyRelatedField(
        queryset=ProductCategory.objects.all(),
        source='category',
        write_only=True,
        required=False,
        allow_null=True
    )
    unit_id = serializers.PrimaryKeyRelatedField(
        queryset=UnitOfMeasure.objects.all(),
        source='unit',
        write_only=True,
        required=False,
        allow_null=True
    )
    product_type_id = serializers.PrimaryKeyRelatedField(
        queryset=ProductType.objects.all(),
        source='product_type',
        write_only=True,
        required=False,
        allow_null=True
    )
    
    class Meta:
        model = Product
        fields = [
            'id', 'part_no', 'legacy_part_no', 'name', 'description',
            'category', 'category_name', 'category_code', 'category_id', 'category_path',
            'product_type', 'product_type_name', 'product_type_code', 'product_type_id',
            'unit', 'unit_name', 'unit_symbol', 'unit_id',
            'hsn_code', 'brand', 'make', 'barcode',
            'weight', 'default_purchase_price', 'default_sale_price',
            'lead_time_days', 'min_stock_level', 'max_stock_level', 'current_balance',
            'is_active', 'is_locked', 'created_by', 'created_by_name',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'part_no', 'created_by', 'created_at', 'updated_at']

    def get_category_path(self, obj):
        if not obj.category:
            return []
        return [node.name for node in obj.category.ancestor_chain()]

    def validate_part_no(self, value):
        """Ensure part_no is unique per tenant (excluding current instance)"""
        if self.instance:
            existing = Product.objects.filter(
                tenant=self.context['request'].tenant,
                part_no=value
            ).exclude(id=self.instance.id)
        else:
            existing = Product.objects.filter(
                tenant=self.context['request'].tenant,
                part_no=value
            )
        
        if existing.exists():
            raise serializers.ValidationError(
                f"Product with part number '{value}' already exists in this tenant."
            )
        return value

    def validate(self, data):
        """Cross-field validation"""
        purchase_price = data.get('default_purchase_price')
        sale_price = data.get('default_sale_price')

        if purchase_price and sale_price and sale_price < purchase_price:
            raise serializers.ValidationError({
                'default_sale_price': 'Sale price cannot be less than purchase price.'
            })

        return data

class ProductSearchSerializer(serializers.ModelSerializer):
    """
    Lightweight serializer used by the quotation product-picker.
    Returns exactly what the frontend needs to auto-fill a line item.
    """

    unit_symbol = serializers.CharField(source="unit.symbol", read_only=True, default="")
    unit_name   = serializers.CharField(source="unit.name",   read_only=True, default="")
    category_name = serializers.CharField(source="category.name", read_only=True, default="")

    class Meta:
        model  = Product
        fields = [
            "id",
            "part_no",
            "legacy_part_no",
            "name",
            "description",
            "hsn_code",
            "unit_symbol",
            "unit_name",
            "category_name",
            "default_sale_price",
            "brand",
            "make",
            "lead_time_days",
        ]