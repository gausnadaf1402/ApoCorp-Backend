# apps/documents/urls.py

from django.urls import path
from .views import QuotationPDFView, ProformaPDFView, LetterheadView

urlpatterns = [
    # Quotation PDF
    path('quotation/<uuid:pk>/pdf/', QuotationPDFView.as_view(), name='quotation-pdf'),
    
    # Proforma PDF
    path('proforma/<uuid:pk>/pdf/', ProformaPDFView.as_view(), name='proforma-pdf'),
    
    # Letterhead config
    path('letterhead/', LetterheadView.as_view(), name='letterhead'),
]