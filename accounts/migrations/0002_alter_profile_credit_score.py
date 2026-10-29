from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='profile',
            name='credit_score',
            field=models.PositiveIntegerField(blank=True, default=None, help_text='Unverified internal score; not a credit-bureau report', null=True),
        ),
    ]
