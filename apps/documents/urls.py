# apps/documents/urls.py

from django.urls import path
from .views import QuotationPDFView, LetterheadView

urlpatterns = [
    # GET  /api/documents/quotation/{id}/pdf/               ← inline preview
    # GET  /api/documents/quotation/{id}/pdf/?download=true ← download
    path('quotation/<uuid:pk>/pdf/', QuotationPDFView.as_view(), name='quotation-pdf'),

    # GET  /api/documents/letterhead/   ← get current config
    # POST /api/documents/letterhead/   ← upload PDF + save company info
    path('letterhead/', LetterheadView.as_view(), name='letterhead'),
]