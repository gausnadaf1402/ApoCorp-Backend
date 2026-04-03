# apps/documents/migrations/0001_initial.py

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('tenants', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='TenantLetterhead',
            fields=[
                ('id', models.AutoField(
                    auto_created=True, primary_key=True, serialize=False
                )),
                ('tenant', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='letterhead',
                    to='tenants.tenant',
                )),
                ('letterhead_pdf', models.FileField(
                    blank=True, null=True,
                    upload_to='letterheads/pdfs/',
                    help_text=(
                        'Blank A4 PDF with the company stationery. '
                        'Header ≤ top 45 mm, footer ≤ bottom 28 mm, '
                        'middle left blank.'
                    ),
                )),
                ('company_name',    models.CharField(max_length=255, blank=True)),
                ('company_address', models.TextField(blank=True)),
                ('company_phone',   models.CharField(max_length=50,  blank=True)),
                ('company_email',   models.EmailField(blank=True)),
                ('company_gstin',   models.CharField(max_length=20,  blank=True)),
                ('company_pan',     models.CharField(max_length=20,  blank=True)),
                ('company_state',   models.CharField(max_length=100, blank=True)),
                ('accent_color',    models.CharField(max_length=7, default='#122C41')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={'verbose_name': 'Tenant Letterhead'},
        ),
    ]