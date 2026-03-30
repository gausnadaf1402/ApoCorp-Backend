from django.contrib import admin
from apps.orders.models import *
# Register your models here.

admin.site.register(OrderAcknowledgement)
admin.site.register(Order)
admin.site.register(OACommercialTerms)
