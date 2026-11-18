from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("payments", "0003_alter_payment_status")]

    operations = [
        migrations.AlterField(
            model_name="payment",
            name="payment_mode",
            field=models.CharField(
                choices=[
                    ("manual", "Manual"),
                    ("auto_debit", "Auto Debit"),
                    ("upi", "UPI"),
                    ("netbanking", "Net Banking"),
                    ("provider", "Configured Provider"),
                ],
                default="manual",
                max_length=20,
            ),
        )
    ]
