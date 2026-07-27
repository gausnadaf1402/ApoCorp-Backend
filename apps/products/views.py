# products/views.py - Add the missing import at the top

from rest_framework.decorators import api_view
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db.models import Q, Sum, Count, Avg, F, Value, CharField
from django.db.models.functions import Coalesce  # ADD THIS IMPORT
from django.utils import timezone
from datetime import timedelta

from core.viewsets import TenantModelViewSet
from core.mixins import ModelPermissionMixin
from apps.accounts.models import TenantUser

from .models import Product, ProductCategory, ProductType, UnitOfMeasure
from .serializers import (
    ProductSerializer, 
    ProductListSerializer,
    ProductCategorySerializer,
    ProductTypeSerializer,
    UnitOfMeasureSerializer,
    ProductSearchSerializer
)

from rest_framework.pagination import PageNumberPagination


# ─────────────────────────────────────────────────────────
# OLD SEARCH VIEW - Keep this for backward compatibility
# ─────────────────────────────────────────────────────────
@api_view(["GET"])
def product_search(request):
    """
    Efficient product search for the quotation product picker.
    
    Query params:
      q        — search term (matches part_no OR name, case-insensitive)
      page     — 1-based page number (default 1)
      limit    — items per page, max 50 (default 20)
      category — UUID of ProductCategory to filter by (optional)
      active   — "true" / "false", default "true"
    
    Returns:
      { results: [...], total: N, page: N, pages: N, has_next: bool }
    """
    
    query    = request.GET.get("q", "").strip()
    category = request.GET.get("category", "").strip()
    active   = request.GET.get("active", "true").lower() != "false"
    
    try:
        limit = min(int(request.GET.get("limit", 20)), 50)
    except ValueError:
        limit = 20
    
    try:
        page = max(int(request.GET.get("page", 1)), 1)
    except ValueError:
        page = 1
    
    # Base queryset — tenant-scoped always
    qs = Product.objects.filter(tenant=request.tenant)
    
    if active:
        qs = qs.filter(is_active=True)
    
    if category:
        qs = qs.filter(category_id=category)
    
    # Full-text style search: split query into tokens so "swas sys" matches "SWAS System"
    if query:
        tokens = query.split()
        for token in tokens:
            qs = qs.filter(
                Q(part_no__icontains=token) |
                Q(legacy_part_no__icontains=token) |
                Q(name__icontains=token)
            )
    
    qs = qs.select_related("unit").order_by("part_no")
    
    total  = qs.count()
    offset = (page - 1) * limit
    items  = qs[offset: offset + limit]
    
    pages = max((total + limit - 1) // limit, 1)
    
    serializer = ProductSearchSerializer(items, many=True)
    
    return Response({
        "results":  serializer.data,
        "total":    total,
        "page":     page,
        "pages":    pages,
        "has_next": page < pages,
    })


class ProductPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'limit'
    max_page_size = 100

# ─────────────────────────────────────────────────────────
# PRODUCT VIEWSET - For CRUD operations
# ─────────────────────────────────────────────────────────
class ProductViewSet(ModelPermissionMixin, TenantModelViewSet):
    """
    Complete CRUD ViewSet for Product management.
    
    Provides:
    - list: GET /api/products/
    - create: POST /api/products/
    - retrieve: GET /api/products/{id}/
    - update: PUT /api/products/{id}/
    - partial_update: PATCH /api/products/{id}/
    - destroy: DELETE /api/products/{id}/
    
    Additional actions:
    - bulk_delete: POST /api/products/bulk_delete/
    - toggle_active: POST /api/products/{id}/toggle_active/
    - toggle_lock: POST /api/products/{id}/toggle_lock/
    - categories: GET /api/products/categories/
    - product_types: GET /api/products/product_types/
    - units: GET /api/products/units/
    - stats: GET /api/products/stats/
    - duplicate: POST /api/products/{id}/duplicate/
    """
    
    queryset = Product.objects.select_related(
        'category', 
        'product_type', 
        'unit', 
        'created_by'
    )
    permission_classes = [IsAuthenticated]
    pagination_class = ProductPagination  # ADD THIS
    
    def get_serializer_class(self):
        """Use different serializers for list vs detail views"""
        if self.action == 'list':
            return ProductListSerializer
        return ProductSerializer
    
    def _get_tenant_user(self):
        """Get tenant user for role-based permissions"""
        return TenantUser.objects.filter(
            user=self.request.user,
            tenant=self.request.tenant
        ).first()
    
    def get_queryset(self):
        """
        Filter products based on user role and query params.
        Managers see all products, employees see only active products.
        """
        queryset = super().get_queryset()
        tenant_user = self._get_tenant_user()
        
        # Role-based filtering
        if tenant_user and tenant_user.role == 'employee':
            queryset = queryset.filter(is_active=True)
        
        # Filter by category
        category_id = self.request.query_params.get('category')
        if category_id:
            queryset = queryset.filter(category_id=category_id)
        
        # Filter by product type
        product_type_id = self.request.query_params.get('product_type')
        if product_type_id:
            queryset = queryset.filter(product_type_id=product_type_id)
        
        # Search functionality
        search_term = self.request.query_params.get('search')
        if search_term:
            queryset = queryset.filter(
                Q(part_no__icontains=search_term) |
                Q(legacy_part_no__icontains=search_term) |
                Q(name__icontains=search_term) |
                Q(brand__icontains=search_term) |
                Q(barcode__icontains=search_term)
            )
        
        return queryset
    
    def perform_create(self, serializer):
        """Set created_by on product creation"""
        serializer.save(
            created_by=self.request.user,
            tenant=self.request.tenant
        )
    
    def perform_update(self, serializer):
        """Check if product is locked before updating"""
        instance = self.get_object()
        
        if instance.is_locked:
            tenant_user = self._get_tenant_user()
            if not tenant_user or tenant_user.role != 'manager':
                raise PermissionDenied(
                    "This product is locked and cannot be modified. "
                    "Only managers can modify locked products."
                )
        
        serializer.save(updated_at=timezone.now())
    
    def perform_destroy(self, instance):
        """Soft delete or hard delete based on user role"""
        tenant_user = self._get_tenant_user()
        
        # Check if product is locked
        if instance.is_locked:
            if not tenant_user or tenant_user.role != 'manager':
                raise PermissionDenied(
                    "Locked products cannot be deleted. Only managers can delete locked products."
                )
        
        # Check if product is used in any quotations or orders
        # (Add your business logic here)
        if hasattr(instance, 'quotation_items') and instance.quotation_items.exists():
            if not tenant_user or tenant_user.role != 'manager':
                raise PermissionDenied(
                    "This product is used in quotations and cannot be deleted. "
                    "Consider marking it as inactive instead."
                )
        
        # Hard delete (or implement soft delete by setting is_active=False)
        instance.delete()
    
    # ─────────────────────────────────────────────────────────
    # Bulk Operations
    # ─────────────────────────────────────────────────────────
    
    @action(detail=False, methods=['post'])
    def bulk_delete(self, request):
        """
        Delete multiple products at once.
        
        Request body:
        {
            "product_ids": ["uuid1", "uuid2", "uuid3"]
        }
        """
        tenant_user = self._get_tenant_user()
        if not tenant_user or tenant_user.role != 'manager':
            raise PermissionDenied("Only managers can perform bulk delete operations.")
        
        product_ids = request.data.get('product_ids', [])
        if not product_ids:
            raise ValidationError({"product_ids": "This field is required."})
        
        products = Product.objects.filter(
            id__in=product_ids,
            tenant=request.tenant
        )
        
        # Check for locked products
        locked_products = products.filter(is_locked=True)
        if locked_products.exists():
            raise ValidationError({
                "locked_products": f"Cannot delete locked products: {list(locked_products.values_list('part_no', flat=True))}"
            })
        
        deleted_count = products.delete()[0]
        
        return Response({
            "message": f"Successfully deleted {deleted_count} product(s).",
            "deleted_count": deleted_count
        })
    
    @action(detail=False, methods=['post'])
    def bulk_update_category(self, request):
        """
        Bulk update category for multiple products.
        
        Request body:
        {
            "product_ids": ["uuid1", "uuid2"],
            "category_id": "category_uuid"
        }
        """
        tenant_user = self._get_tenant_user()
        if not tenant_user or tenant_user.role != 'manager':
            raise PermissionDenied("Only managers can perform bulk update operations.")
        
        product_ids = request.data.get('product_ids', [])
        category_id = request.data.get('category_id')
        
        if not product_ids:
            raise ValidationError({"product_ids": "This field is required."})
        if not category_id:
            raise ValidationError({"category_id": "This field is required."})
        
        # Verify category exists
        try:
            category = ProductCategory.objects.get(id=category_id, tenant=request.tenant)
        except ProductCategory.DoesNotExist:
            raise ValidationError({"category_id": "Category not found."})
        
        # Exclude locked products
        products = Product.objects.filter(
            id__in=product_ids,
            tenant=request.tenant,
            is_locked=False
        )
        
        updated_count = products.update(category=category, updated_at=timezone.now())
        
        return Response({
            "message": f"Successfully updated category for {updated_count} product(s).",
            "updated_count": updated_count,
            "skipped": len(product_ids) - updated_count
        })
    
    # ─────────────────────────────────────────────────────────
    # Product Actions
    # ─────────────────────────────────────────────────────────
    
    @action(detail=True, methods=['post'])
    def toggle_active(self, request, pk=None):
        """Toggle product active status"""
        product = self.get_object()
        
        # Check permissions
        tenant_user = self._get_tenant_user()
        if product.is_locked and tenant_user and tenant_user.role != 'manager':
            raise PermissionDenied("Cannot modify locked products.")
        
        product.is_active = not product.is_active
        product.save(update_fields=['is_active', 'updated_at'])
        
        return Response({
            "message": f"Product {product.part_no} is now {'active' if product.is_active else 'inactive'}.",
            "is_active": product.is_active
        })
    
    @action(detail=True, methods=['post'])
    def toggle_lock(self, request, pk=None):
        """Toggle product lock status (managers only)"""
        tenant_user = self._get_tenant_user()
        if not tenant_user or tenant_user.role != 'manager':
            raise PermissionDenied("Only managers can lock/unlock products.")
        
        product = self.get_object()
        product.is_locked = not product.is_locked
        product.save(update_fields=['is_locked', 'updated_at'])
        
        return Response({
            "message": f"Product {product.part_no} is now {'locked' if product.is_locked else 'unlocked'}.",
            "is_locked": product.is_locked
        })
    
    @action(detail=True, methods=['post'])
    def duplicate(self, request, pk=None):
        """
        Duplicate an existing product.
        Auto-generates new part number.
        """
        tenant_user = self._get_tenant_user()
        if not tenant_user:
            raise PermissionDenied("You don't have permission to duplicate products.")
        
        original_product = self.get_object()
        
        # Create a copy
        duplicated_product = Product(
            tenant=request.tenant,
            name=f"{original_product.name} (Copy)",
            description=original_product.description,
            category=original_product.category,
            product_type=original_product.product_type,
            unit=original_product.unit,
            hsn_code=original_product.hsn_code,
            brand=original_product.brand,
            make=original_product.make,
            weight=original_product.weight,
            default_purchase_price=original_product.default_purchase_price,
            default_sale_price=original_product.default_sale_price,
            lead_time_days=original_product.lead_time_days,
            min_stock_level=original_product.min_stock_level,
            max_stock_level=original_product.max_stock_level,
            is_active=True,
            is_locked=False,
            created_by=request.user
        )
        # Part number will be auto-generated in save()
        duplicated_product.save()
        
        serializer = self.get_serializer(duplicated_product)
        return Response({
            "message": f"Product duplicated successfully. New part number: {duplicated_product.part_no}",
            "product": serializer.data
        }, status=201)
    
    # ─────────────────────────────────────────────────────────
    # Reference Data Endpoints
    # ─────────────────────────────────────────────────────────
    
    @action(detail=False, methods=['get'], url_path='categories', url_name='categories')
    def categories(self, request):
        """Get all product categories for dropdowns"""
        categories = ProductCategory.objects.filter(
            tenant=request.tenant,
            is_active=True
        ).order_by('name')
        
        serializer = ProductCategorySerializer(categories, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'], url_path='product_types', url_name='product_types')
    def product_types(self, request):
        """Get all product types for dropdowns"""
        product_types = ProductType.objects.filter(
            tenant=request.tenant,
            is_active=True
        ).order_by('name')
        
        serializer = ProductTypeSerializer(product_types, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'], url_path='units', url_name='units')
    def units(self, request):
        """Get all units of measure for dropdowns"""
        units = UnitOfMeasure.objects.filter(
            tenant=request.tenant,
            is_active=True
        ).order_by('name')
        
        serializer = UnitOfMeasureSerializer(units, many=True)
        return Response(serializer.data)
    
    # ─────────────────────────────────────────────────────────
    # Statistics Endpoint
    # ─────────────────────────────────────────────────────────
    
    # products/views.py - Update the stats method

    @action(detail=False, methods=['get'])
    def stats(self, request):
        """
        Get product statistics without pagination limitations.
        """
        queryset = self.get_queryset()
        
        # Calculate total value - try purchase price first, then sale price
        total_value = queryset.aggregate(
            total_purchase=Sum('default_purchase_price'),
            total_sale=Sum('default_sale_price')
        )
        
        # Use purchase price if available, otherwise use sale price
        total_value_amount = total_value['total_purchase'] or total_value['total_sale'] or 0
        
        # Calculate base stats using database aggregation
        stats_data = {
            "total_products": queryset.count(),
            "active_products": queryset.filter(is_active=True).count(),
            "inactive_products": queryset.filter(is_active=False).count(),
            "locked_products": queryset.filter(is_locked=True).count(),
            "categories_count": ProductCategory.objects.filter(
                tenant=request.tenant,
                is_active=True
            ).count(),
            "total_value": float(total_value_amount) if total_value_amount else 0,
        }
        
        # Products by category (top 5)
        products_by_category = queryset.filter(
            category__isnull=False
        ).values('category__name').annotate(
            count=Count('id')
        ).order_by('-count')[:5]
        
        stats_data['products_by_category'] = list(products_by_category)
        
        # Recent additions (last 30 days)
        thirty_days_ago = timezone.now() - timedelta(days=30)
        stats_data['recently_added'] = queryset.filter(
            created_at__gte=thirty_days_ago
        ).count()
        
        # Total inventory value (using purchase price if available, else sale price)
        inventory_value = queryset.aggregate(
            total_purchase=Sum('default_purchase_price'),
            total_sale=Sum('default_sale_price')
        )
        inventory_amount = inventory_value['total_purchase'] or inventory_value['total_sale'] or 0
        stats_data['total_inventory_value'] = float(inventory_amount) if inventory_amount else 0
        
        return Response(stats_data)
    
    # products/views.py - Add to ProductViewSet

    @action(detail=False, methods=['get'], url_path='dashboard-preview')
    def dashboard_preview(self, request):
        """
        Get a small preview of products for the dashboard.
        Returns 6 products with basic info.
        """
        queryset = self.get_queryset().order_by('-created_at')[:6]
        serializer = ProductListSerializer(queryset, many=True)
        return Response(serializer.data)