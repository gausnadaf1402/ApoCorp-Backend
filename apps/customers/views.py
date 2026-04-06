# customers/views.py
from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db.models import Q, Sum, Count, Avg
from django.utils import timezone
from django.core.cache import cache

from core.viewsets import TenantModelViewSet
from core.mixins import ModelPermissionMixin
from .models import Customer
from .serializers import CustomerSerializer, CustomerReadSerializer, CustomerDropdownSerializer


class CustomerViewSet(ModelPermissionMixin, TenantModelViewSet):

    queryset = Customer.objects.all()
    
    def get_serializer_class(self):
        if self.action == "list":
            return CustomerReadSerializer
        elif self.action == "search":
            # Check if we need full details
            if self.request and self.request.query_params.get('detail', 'false').lower() == 'true':
                return CustomerReadSerializer
            return CustomerDropdownSerializer
        return CustomerSerializer
    
    permission_classes = [IsAuthenticated]

    @action(detail=True, methods=['post'])
    def lock(self, request, pk=None):
        customer = self.get_object()
        customer.is_locked = True
        customer.locked_at = timezone.now()
        customer.locked_by = request.user
        customer.save()
        return Response({'status': 'locked'})

    @action(detail=True, methods=['post'])
    def unlock(self, request, pk=None):
        customer = self.get_object()
        customer.is_locked = False
        customer.locked_at = None
        customer.locked_by = None
        customer.save()
        return Response({'status': 'unlocked'})

    @action(detail=False, methods=['get'])
    def stats(self, request):
        """
        Get customer statistics without pagination limitations.
        
        Query params (optional filters):
        q        — search term (matches company_name, email, customer_code, phone)
        tier     — filter by tier (A, B, C)
        location — filter by city or country
        
        Returns:
        {
            "active": 1234,
            "inactive": 56,
            "domestic": 789,
            "offshore": 501,
            "total": 1290,
            "tier_breakdown": {"A": 100, "B": 200, "C": 990},
            "filters_applied": {...}
        }
        """
        
        # Get filter parameters (same as search endpoint)
        query = request.GET.get("q", "").strip()
        tier = request.GET.get("tier", "").strip()
        location = request.GET.get("location", "").strip()
        
        # Base queryset — tenant-scoped always
        qs = Customer.objects.filter(tenant=request.tenant)
        
        # Apply filters if they exist (same logic as search endpoint)
        if query:
            tokens = query.split()
            for token in tokens:
                qs = qs.filter(
                    Q(company_name__icontains=token) |
                    Q(email__icontains=token) |
                    Q(customer_code__icontains=token) |
                    Q(telephone_primary__icontains=token)
                )
        
        if tier and tier in ['A', 'B', 'C']:
            qs = qs.filter(tier=tier)
        
        if location:
            location_lower = location.lower()
            qs = qs.filter(
                Q(city__icontains=location_lower) |
                Q(country__icontains=location_lower)
            )
        
        # Calculate stats using database aggregation (no pagination)
        total = qs.count()
        
        # Active: is_active=True and is_locked=False
        active = qs.filter(is_active=True, is_locked=False).count()
        
        # Inactive: is_locked=True or is_active=False
        inactive = qs.filter(Q(is_locked=True) | Q(is_active=False)).count()
        
        # Domestic: country is India (case insensitive)
        domestic = qs.filter(country__iexact='india').count()
        
        # Offshore: country exists and is not India
        offshore = qs.exclude(country__iexact='india').exclude(country='').count()
        
        # Optional: Tier breakdown for additional insights
        tier_breakdown = {
            'A': qs.filter(tier='A').count(),
            'B': qs.filter(tier='B').count(),
            'C': qs.filter(tier='C').count(),
        }
        
        # Optional: Calculate total lifetime value
        total_lifetime_value = qs.aggregate(total=Sum('lifetime_value'))['total'] or 0
        
        response_data = {
            "active": active,
            "inactive": inactive,
            "domestic": domestic,
            "offshore": offshore,
            "total": total,
            "tier_breakdown": tier_breakdown,
            "total_lifetime_value": float(total_lifetime_value),
            "filters_applied": {
                "search": query if query else None,
                "tier": tier if tier else None,
                "location": location if location else None,
            }
        }
        
        return Response(response_data)

    @action(detail=False, methods=['get'])
    def search(self, request):
        """
        Efficient customer search with pagination for dropdowns.
        
        Query params:
        q        — search term (matches company_name, email, customer_code, phone)
        tier     — filter by tier (A, B, C)
        location — filter by city or country
        page     — 1-based page number (default 1)
        limit    — items per page, max 50 (default 20)
        detail   — if 'true', returns full customer data (default 'false')
        
        Returns:
        { results: [...], total: N, page: N, pages: N, has_next: bool }
        """
        
        query = request.GET.get("q", "").strip()
        tier = request.GET.get("tier", "").strip()
        location = request.GET.get("location", "").strip()
        detail = request.GET.get("detail", "false").lower() == "true"
        
        try:
            limit = min(int(request.GET.get("limit", 20)), 50)
        except ValueError:
            limit = 20
        
        try:
            page = max(int(request.GET.get("page", 1)), 1)
        except ValueError:
            page = 1
        
        # Base queryset — tenant-scoped always
        qs = Customer.objects.filter(tenant=request.tenant, is_active=True)
        
        # Search functionality
        if query:
            tokens = query.split()
            for token in tokens:
                qs = qs.filter(
                    Q(company_name__icontains=token) |
                    Q(email__icontains=token) |
                    Q(customer_code__icontains=token) |
                    Q(telephone_primary__icontains=token)
                )
        
        # Tier filter
        if tier and tier in ['A', 'B', 'C']:
            qs = qs.filter(tier=tier)
        
        # Location filter (city or country)
        if location:
            location_lower = location.lower()
            qs = qs.filter(
                Q(city__icontains=location_lower) |
                Q(country__icontains=location_lower)
            )
        
        # Optimize with select_related/prefetch_related for related data
        qs = qs.select_related('account_manager').prefetch_related('pocs', 'addresses')
        qs = qs.order_by("-customer_code")
        
        total = qs.count()
        offset = (page - 1) * limit
        items = qs[offset: offset + limit]
        
        pages = max((total + limit - 1) // limit, 1)
        
        # Use appropriate serializer based on detail parameter
        if detail:
            serializer = CustomerReadSerializer(items, many=True)
        else:
            serializer = CustomerDropdownSerializer(items, many=True)
        
        response_data = {
            "results": serializer.data,
            "total": total,
            "page": page,
            "pages": pages,
            "has_next": page < pages,
        }
        
        return Response(response_data)