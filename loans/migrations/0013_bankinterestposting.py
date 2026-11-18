from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("loans", "0012_rename_interest_indexes"),
        ("payments", "0005_prepayment_components"),
    ]

    operations = [
        migrations.CreateModel(
            name="BankInterestPosting",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("posting_date", models.DateField()),
                ("period_start", models.DateField(blank=True, null=True)),
                ("period_end", models.DateField(blank=True, null=True)),
                ("amount", models.DecimalField(decimal_places=2, max_digits=15)),
                ("recovered_amount", models.DecimalField(decimal_places=2, default=0, max_digits=15)),
                ("reference", models.CharField(max_length=120, unique=True)),
                ("remarks", models.TextField(blank=True)),
                ("status", models.CharField(choices=[("pending", "Pending"), ("part_paid", "Part Paid"), ("recovered", "Recovered")], default="pending", max_length=20)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("loan", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="bank_interest_postings", to="loans.loan")),
                ("payment", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="bank_interest_postings", to="payments.payment")),
                ("prepayment", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="bank_interest_postings", to="payments.prepayment")),
            ],
            options={"ordering": ["posting_date", "id"]},
        ),
        migrations.AddIndex(
            model_name="bankinterestposting",
            index=models.Index(fields=["loan", "posting_date"], name="loans_banki_loan_date_idx"),
        ),
        migrations.AddIndex(
            model_name="bankinterestposting",
            index=models.Index(fields=["loan", "status"], name="loans_banki_loan_status_idx"),
        ),
    ]
