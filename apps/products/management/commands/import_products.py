import pandas as pd
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User

from apps.tenants.models import Tenant
from apps.products.models import Product, ProductCategory, UnitOfMeasure, ProductType


# ─────────────────────────────────────────────────────────
# Cleaning helpers
# ─────────────────────────────────────────────────────────

def clean_int(value):
    if pd.isna(value) or str(value).strip() in ["", "-", "NULL"]:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def clean_decimal(value):
    if pd.isna(value) or str(value).strip() in ["", "-", "NULL"]:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def clean_str(value):
    if pd.isna(value):
        return ""
    return str(value).strip()


# "Item Type" has junk values left over from the old ERP export
# (e.g. "-", "1", "0", "_") mixed in with real codes like "B-Bought".
JUNK_ITEM_TYPES = {"", "-", "_", "0", "1", "nan", "none", "null"}


def clean_item_type(value):
    val = clean_str(value)
    if val.lower() in JUNK_ITEM_TYPES:
        return ""
    return val


class Command(BaseCommand):

    help = "Import products from Part_Master_Final.xlsx (new CAT.TYPE part-no format)"

    def add_arguments(self, parser):
        parser.add_argument("file_path", type=str)
        parser.add_argument(
            "--sheet",
            type=str,
            default="Part Master",
            help="Sheet name to import (default: 'Part Master')",
        )

    def handle(self, *args, **kwargs):

        file_path = kwargs["file_path"]
        sheet_name = kwargs["sheet"]

        # header=2 because the sheet has a 2-row banner above the real header
        df = pd.read_excel(file_path, sheet_name=sheet_name, header=2)
        df.columns = df.columns.str.strip()

        print("Columns detected:", df.columns.tolist())

        # Section-divider rows (e.g. "▌ 1. MECHANICAL", "→ 1.01 Pipes") have
        # no Old Part No. — filter them out.
        df = df[df["Old Part No."].notna()].copy()
        print(f"Data rows after dropping section headers: {len(df)}")

        tenant = Tenant.objects.first()
        user = User.objects.first()

        if not tenant:
            self.stdout.write(self.style.ERROR("No tenant found"))
            return

        created = 0
        skipped = 0

        # Caches so we don't hit the DB for every single row for the same
        # category/type/unit combination.
        category_cache = {}
        product_type_cache = {}
        unit_cache = {}

        for index, row in df.iterrows():

            new_part_no = clean_str(row.get("New Part No."))
            old_part_no = clean_str(row.get("Old Part No."))
            description = clean_str(row.get("Description"))

            l1_name = clean_str(row.get("L1 Category"))
            l2_name = clean_str(row.get("L2 Sub-Category"))
            l3_name = clean_str(row.get("L3 Part Type"))

            item_type = clean_item_type(row.get("Item Type"))
            unit_name = clean_str(row.get("Unit"))

            purchase_price = clean_decimal(row.get("Purchase Price"))
            sale_price = clean_decimal(row.get("Sale Price"))
            min_level = clean_decimal(row.get("Min Level"))
            max_level = clean_decimal(row.get("Max Level"))
            lead_time = clean_int(row.get("Lead Time"))
            balance = clean_decimal(row.get("Balance"))

            if not new_part_no:
                skipped += 1
                continue

            if Product.objects.filter(tenant=tenant, part_no=new_part_no).exists():
                skipped += 1
                continue

            # ── Category tree (L1 -> L2 -> L3) ──────────────────────────
            # CAT / TYPE codes come from the part number itself, e.g.
            # "ME.PP.100NB.SCH10.SMLS" -> cat_code="ME", type_code="PP"
            segments = new_part_no.split(".")
            cat_code = segments[0] if len(segments) > 0 else ""
            type_code = segments[1] if len(segments) > 1 else ""

            category = None
            if l1_name and cat_code:
                cache_key_l1 = (cat_code, None)
                l1_cat = category_cache.get(cache_key_l1)
                if not l1_cat:
                    l1_cat, _ = ProductCategory.objects.get_or_create(
                        tenant=tenant,
                        code=cat_code,
                        parent=None,
                        defaults={"name": l1_name},
                    )
                    category_cache[cache_key_l1] = l1_cat

                category = l1_cat

                if l2_name and type_code:
                    l2_code = f"{cat_code}.{type_code}"
                    cache_key_l2 = (l2_code, l1_cat.id)
                    l2_cat = category_cache.get(cache_key_l2)
                    if not l2_cat:
                        l2_cat, _ = ProductCategory.objects.get_or_create(
                            tenant=tenant,
                            code=l2_code,
                            parent=l1_cat,
                            defaults={"name": l2_name},
                        )
                        category_cache[cache_key_l2] = l2_cat

                    category = l2_cat

                    if l3_name:
                        cache_key_l3 = (l3_name, l2_cat.id)
                        l3_cat = category_cache.get(cache_key_l3)
                        if not l3_cat:
                            l3_cat, _ = ProductCategory.objects.get_or_create(
                                tenant=tenant,
                                code=None,
                                parent=l2_cat,
                                name=l3_name,
                            )
                            category_cache[cache_key_l3] = l3_cat

                        category = l3_cat

            # ── Unit ─────────────────────────────────────────────────────
            unit = None
            if unit_name:
                unit = unit_cache.get(unit_name)
                if not unit:
                    unit, _ = UnitOfMeasure.objects.get_or_create(
                        tenant=tenant,
                        name=unit_name,
                        defaults={"symbol": unit_name},
                    )
                    unit_cache[unit_name] = unit

            # ── Product type ─────────────────────────────────────────────
            product_type = None
            if item_type:
                product_type = product_type_cache.get(item_type)
                if not product_type:
                    product_type, _ = ProductType.objects.get_or_create(
                        tenant=tenant,
                        code=item_type,
                        defaults={"name": item_type},
                    )
                    product_type_cache[item_type] = product_type

            try:
                Product.objects.create(
                    tenant=tenant,
                    part_no=new_part_no,
                    legacy_part_no=old_part_no,
                    name=description[:255] if description else new_part_no,
                    description=description,
                    category=category,
                    product_type=product_type,
                    unit=unit,
                    default_purchase_price=purchase_price,
                    default_sale_price=sale_price,
                    lead_time_days=lead_time,
                    min_stock_level=min_level,
                    max_stock_level=max_level,
                    current_balance=balance,
                    created_by=user,
                )

                created += 1

            except Exception as e:
                skipped += 1
                self.stdout.write(f"Skipped row {index} ({new_part_no}) due to error: {e}")

            if created % 500 == 0 and created != 0:
                self.stdout.write(f"{created} products imported...")

        self.stdout.write(
            self.style.SUCCESS(
                f"Import complete. Created: {created}, Skipped: {skipped}"
            )
        )