# Generated manually: ImageField -> FileField (no Pillow required)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stories', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='story',
            name='cover_image',
            field=models.FileField(blank=True, null=True, upload_to='covers/', verbose_name='Обложка'),
        ),
    ]
