# apps/documents/migrations/XXXX_add_bank_details_to_tenantletterhead.py
# Rename XXXX to your next migration number, e.g. 0002

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        # Replace with your actual last migration in this app
        ('documents', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='tenantletterhead',
            name='bank_name',
            field=models.CharField(blank=True, max_length=255,
                                   help_text='e.g. State Bank of India'),
        ),
        migrations.AddField(
            model_name='tenantletterhead',
            name='bank_account_name',
            field=models.CharField(blank=True, max_length=255,
                                   help_text='Account holder name as per bank records'),
        ),
        migrations.AddField(
            model_name='tenantletterhead',
            name='bank_branch',
            field=models.CharField(blank=True, max_length=255,
                                   help_text='Branch name / code'),
        ),
        migrations.AddField(
            model_name='tenantletterhead',
            name='bank_account_number',
            field=models.CharField(blank=True, max_length=50,
                                   help_text='Bank account number'),
        ),
        migrations.AddField(
            model_name='tenantletterhead',
            name='bank_ifsc',
            field=models.CharField(blank=True, max_length=20,
                                   help_text='IFSC code'),
        ),
        migrations.AddField(
            model_name='tenantletterhead',
            name='bank_micr',
            field=models.CharField(blank=True, max_length=20,
                                   help_text='MICR code (optional)'),
        ),
    ]