from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("loans", "0011_loaninterestaccrual_prepayment"),
    ]

    operations = [
        migrations.RenameIndex(
            model_name="loaninterestaccrual",
            new_name="loan_intere_loan_id_5b0a1a_idx",
            old_name="loans_loanin_loan_id_4d7dc6_idx",
        ),
        migrations.RenameIndex(
            model_name="loaninterestaccrual",
            new_name="loan_intere_loan_id_4a1025_idx",
            old_name="loans_loanin_loan_id_5f5b8b_idx",
        ),
        migrations.RenameIndex(
            model_name="loaninterestaccrual",
            new_name="loan_intere_disburs_ba4190_idx",
            old_name="loans_loanin_disburs_58d8a4_idx",
        ),
    ]
