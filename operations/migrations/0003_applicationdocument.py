import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("operations", "0002_alter_application_options"),
    ]

    operations = [
        migrations.CreateModel(
            name="ApplicationDocument",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("identity", "Identity proof"),
                            ("income", "Income proof"),
                            ("other", "Other"),
                        ],
                        max_length=20,
                    ),
                ),
                ("file", models.FileField(upload_to="application_documents/%Y/%m/")),
                ("uploaded_at", models.DateTimeField(auto_now_add=True)),
                (
                    "application",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="attachments",
                        to="operations.application",
                    ),
                ),
            ],
        ),
    ]
