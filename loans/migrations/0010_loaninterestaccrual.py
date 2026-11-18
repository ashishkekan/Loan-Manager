from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("loans", "0009_loandisbursement_released_by_and_more"),
        ("payments", "0003_alter_payment_status"),
    ]

    operations = [
        migrations.CreateModel(
            name="LoanInterestAccrual",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("accrual_date", models.DateField()),
                ("principal_balance", models.DecimalField(decimal_places=2, max_digits=15)),
                ("disbursed_amount", models.DecimalField(decimal_places=2, max_digits=15)),
                ("annual_interest_rate", models.DecimalField(decimal_places=4, max_digits=7)),
                ("interest_amount", models.DecimalField(decimal_places=2, max_digits=15)),
                ("recovered_amount", models.DecimalField(decimal_places=2, default=0, max_digits=15)),
                ("status", models.CharField(choices=[("pending", "Pending"), ("part_paid", "Part Paid"), ("recovered", "Recovered")], default="pending", max_length=20)),
                ("recovered_on", models.DateField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("disbursement", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="interest_accruals", to="loans.loandisbursement")),
                ("loan", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="interest_accruals", to="loans.loan")),
                ("payment", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="interest_accruals", to="payments.payment")),
            ],
            options={
                "db_table": "loan_interest_accruals",
                "ordering": ["accrual_date", "id"],
            },
        ),
        migrations.AddConstraint(
            model_name="loaninterestaccrual",
            constraint=models.UniqueConstraint(fields=("loan", "disbursement", "accrual_date"), name="unique_daily_loan_interest_accrual"),
        ),
        migrations.AddIndex(
            model_name="loaninterestaccrual",
            index=models.Index(fields=["loan", "accrual_date"], name="loans_loanin_loan_id_4d7dc6_idx"),
        ),
        migrations.AddIndex(
            model_name="loaninterestaccrual",
            index=models.Index(fields=["loan", "status"], name="loans_loanin_loan_id_5f5b8b_idx"),
        ),
        migrations.AddIndex(
            model_name="loaninterestaccrual",
            index=models.Index(fields=["disbursement", "accrual_date"], name="loans_loanin_disburs_58d8a4_idx"),
        ),
    ]
