# apps/documents/admin.py

from django.contrib import admin
from django.utils.html import format_html
from .models import TenantLetterhead


@admin.register(TenantLetterhead)
class TenantLetterheadAdmin(admin.ModelAdmin):

    list_display = (
        'tenant', 'company_name', 'company_email',
        'company_gstin', 'has_letterhead_pdf', 'accent_preview', 'updated_at',
    )

    search_fields = ('tenant__company_name', 'company_name', 'company_gstin')

    readonly_fields = ('created_at', 'updated_at', 'letterhead_pdf_preview')

    fieldsets = (
        ('Tenant', {
            'fields': ('tenant',),
        }),
        ('Letterhead PDF', {
            'description': (
                'Upload a blank A4 PDF — your pre-designed stationery. '
                'Header must fit within the top 58 mm, footer within the '
                'bottom 28 mm. Leave the middle completely blank.'
            ),
            'fields': ('letterhead_pdf', 'letterhead_pdf_preview'),
        }),
        ('Company Info  (shown in the "From" block of every quotation)', {
            'fields': (
                'company_name', 'company_address',
                'company_phone', 'company_email',
                'company_gstin', 'company_pan', 'company_state',
            ),
        }),
        ('Bank Details  (printed at the bottom of every quotation)', {
            'fields': (
                'bank_name', 'bank_account_name', 'bank_branch',
                'bank_account_number', 'bank_ifsc', 'bank_micr',
            ),
        }),
        ('Branding', {
            'fields': ('accent_color',),
        }),
        ('Timestamps', {
            'classes': ('collapse',),
            'fields': ('created_at', 'updated_at'),
        }),
    )

    # ── Custom columns ────────────────────────────────────────────────────────

    @admin.display(description='Letterhead PDF', boolean=True)
    def has_letterhead_pdf(self, obj):
        return bool(obj.letterhead_pdf)

    @admin.display(description='Accent')
    def accent_preview(self, obj):
        color = obj.accent_color or '#122C41'
        return format_html(
            '<span style="display:inline-flex;align-items:center;gap:6px;">'
            '<span style="width:16px;height:16px;border-radius:3px;'
            'background:{};border:1px solid #ccc;"></span>{}</span>',
            color, color,
        )

    @admin.display(description='Current PDF')
    def letterhead_pdf_preview(self, obj):
        if obj.letterhead_pdf:
            return format_html(
                '<a href="{}" target="_blank">📄 View uploaded letterhead PDF</a>',
                obj.letterhead_pdf.url,
            )
        return '— No PDF uploaded yet'