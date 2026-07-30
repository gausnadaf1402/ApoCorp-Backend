from rest_framework.routers import DefaultRouter
from .views import OrderAcknowledgementViewSet, OrderViewSet, OAAttachmentViewSet

router = DefaultRouter()

router.register(r'oa', OrderAcknowledgementViewSet, basename='oa')
router.register(r'orders', OrderViewSet, basename='orders')
router.register(r'oa-attachments', OAAttachmentViewSet, basename='oa-attachments')

urlpatterns = router.urls