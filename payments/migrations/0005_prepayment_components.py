from decimal import Decimal

from django.db import migrations, models


def backfill_principal_component(apps, schema_editor):
    Prepayment = apps.get_model("payments", "Prepayment")
    Prepayment.objects.filter(principal_component=Decimal("0.00")).update(
        principal_component=models.F("amount")
    )


class Migration(migrations.Migration):
    dependencies = [("payments", "0004_payment_provider_mode")]

    operations = [
        migrations.AddField(
            model_name="prepayment",
            name="interest_component",
            field=models.DecimalField(
                decimal_places=2, default=Decimal("0.00"), max_digits=15
            ),
        ),
        migrations.AddField(
            model_name="prepayment",
            name="principal_component",
            field=models.DecimalField(
                decimal_places=2, default=Decimal("0.00"), max_digits=15
            ),
        ),
        migrations.RunPython(backfill_principal_component, migrations.RunPython.noop),
    ]
