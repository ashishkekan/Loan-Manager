from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("operations", "0001_initial"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="application",
            options={
                "permissions": [
                    ("approve_application", "Approve loan applications"),
                    ("approve_disbursement", "Approve disbursements"),
                ]
            },
        ),
    ]
