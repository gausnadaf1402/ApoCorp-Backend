import re
import uuid
from django.db import models
from django.contrib.auth.models import User
from core.mixins import TenantModelMixin


class ProductCategory(TenantModelMixin):
    """
    Now used as a 3-level tree:
      L1 (parent=None)      e.g. code="ME",       name="1. MECHANICAL"
      L2 (parent=L1)        e.g. code="ME.PP",    name="1.01 Pipes"
      L3 (parent=L2, leaf)  e.g. code=None,       name="Pipe"

    Product.category always points at the L3 leaf node.
    L1/L2 codes come straight from the first two dot-segments of
    "New Part No." in the import sheet (e.g. ME.PP.100NB.SCH10.SMLS).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    name = models.CharField(max_length=255)
    code = models.CharField(max_length=50, null=True, blank=True)

    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="children"
    )

    description = models.TextField(blank=True)

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["tenant", "name"]),
        ]
        constraints = [
            # Prevents duplicate categories being created on repeat imports.
            # Postgres treats NULL codes as distinct, so L3 leaves (code=None)
            # aren't affected by this constraint.
            models.UniqueConstraint(
                fields=["tenant", "parent", "code"],
                name="unique_category_code_per_parent",
                condition=models.Q(code__isnull=False),
            )
        ]

    def __str__(self):
        return self.name

    def ancestor_chain(self):
        """Returns [L1, L2, L3...] from root down to self."""
        chain = [self]
        node = self
        while node.parent_id:
            node = node.parent
            chain.insert(0, node)
        return chain


class UnitOfMeasure(TenantModelMixin):

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    name = models.CharField(max_length=100)
    symbol = models.CharField(max_length=20)

    description = models.CharField(max_length=255, blank=True)

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["tenant", "name"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.symbol})"


class ProductType(TenantModelMixin):

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    code = models.CharField(max_length=50)
    name = models.CharField(max_length=100)

    description = models.TextField(blank=True)

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "code"],
                name="unique_product_type_per_tenant"
            )
        ]

    def __str__(self):
        return self.name


class Product(TenantModelMixin):

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    part_no = models.CharField(max_length=100, blank=True)

    # Old ERP part number (e.g. "01PP100NS4SCH10"), kept for traceability /
    # so old part numbers remain searchable after the re-numbering.
    legacy_part_no = models.CharField(max_length=100, blank=True, db_index=True)

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)

    category = models.ForeignKey(
        ProductCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="products"
    )

    product_type = models.ForeignKey(
        ProductType,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    unit = models.ForeignKey(
        UnitOfMeasure,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    hsn_code = models.CharField(max_length=50, blank=True)

    brand = models.CharField(max_length=100, blank=True)
    make = models.CharField(max_length=100, blank=True)

    barcode = models.CharField(max_length=100, blank=True)

    weight = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True
    )

    default_purchase_price = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        null=True,
        blank=True
    )

    default_sale_price = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        null=True,
        blank=True
    )

    lead_time_days = models.IntegerField(null=True, blank=True)

    # ✅ NEW FIELDS from Part_Master_Final.xlsx
    min_stock_level = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True
    )
    max_stock_level = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True
    )
    current_balance = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True,
        help_text="Snapshot balance at time of import, not live inventory."
    )

    is_active = models.BooleanField(default=True)
    is_locked = models.BooleanField(default=False)

    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "part_no"],
                name="unique_part_per_tenant"
            )
        ]

        indexes = [
            models.Index(fields=["tenant", "part_no"]),
            models.Index(fields=["tenant", "name"]),
        ]

    def __str__(self):
        return f"{self.part_no} - {self.name}"

    def _l2_prefix(self):
        """
        Walk up from self.category to the L2 node (the one whose parent
        is a root L1 node) and return its code, e.g. "ME.PP".
        Falls back to whatever level is actually assigned.
        """
        node = self.category
        if not node:
            return None

        chain = node.ancestor_chain()  # [L1, L2, L3] or shorter

        # Prefer L2 (index 1) if it exists, else L1, else the leaf itself.
        if len(chain) >= 2:
            target = chain[1]
        else:
            target = chain[0]

        return target.code or target.name

    def _name_code(self):
        """
        First 3 letters of the product name, cleaned up — e.g.
        "Rubber Bush 09" -> "RUB". Falls back to "GEN" if the name has
        no usable letters (shouldn't normally happen since name is required).
        """
        letters = re.sub(r"[^A-Za-z]", "", self.name or "")
        return (letters[:3] or "GEN").upper()

    def generate_part_no(self):
        """
        New format: "<CAT>.<TYPE>.<NAMECODE>.<NNN>" e.g. "ME.FIT.RUB.028" —
        NAMECODE is a 3-letter hint from the product name so placeholder
        numbers are at least skimmable, NNN keeps counting continuously
        across the whole L2 category (not reset per name) so numbers don't
        collide or restart confusingly. This is only a placeholder for
        manually-created products with no full spec yet — users should
        still edit it to the full CAT.TYPE.SUBTYPE.MATERIAL.SPEC form when
        the real spec is known.

        Falls back to the legacy "PRD-00001" sequence if no category is set.
        """
        prefix = self._l2_prefix()

        if not prefix:
            last_product = (
                Product.objects
                .filter(tenant=self.tenant, part_no__startswith="PRD-")
                .order_by("-part_no")
                .first()
            )
            if last_product and last_product.part_no:
                try:
                    last_number = int(last_product.part_no.split("-")[1])
                except (IndexError, ValueError):
                    last_number = 0
            else:
                last_number = 0
            return f"PRD-{last_number + 1:05d}"

        name_code = self._name_code()

        # Scan every part_no under this L2 prefix and pull out its trailing
        # number — comparing part_no strings directly isn't reliable once a
        # name-code segment sits in the middle (e.g. "ME.FIT.RUB.028" vs
        # "ME.FIT.VAL.005" don't sort the way their numbers would suggest).
        existing = Product.objects.filter(
            tenant=self.tenant, part_no__startswith=f"{prefix}."
        ).values_list("part_no", flat=True)

        last_number = 0
        for part_no in existing:
            match = re.search(r"\.(\d+)$", part_no)
            if match:
                last_number = max(last_number, int(match.group(1)))

        return f"{prefix}.{name_code}.{last_number + 1:03d}"

    def save(self, *args, **kwargs):

        if not self.part_no:
            self.part_no = self.generate_part_no()

        super().save(*args, **kwargs)
