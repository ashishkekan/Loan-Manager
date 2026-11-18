from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("loans", "0010_loaninterestaccrual"),
        ("payments", "0005_prepayment_components"),
    ]

    operations = [
        migrations.AddField(
            model_name="loaninterestaccrual",
            name="prepayment",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="interest_accruals",
                to="payments.prepayment",
            ),
        ),
    ]
