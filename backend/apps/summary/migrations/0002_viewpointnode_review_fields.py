# Generated manually (no live Django env available to run makemigrations —
# hand-written to match Django 6.0's migration format).

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('summary', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='viewpointnode',
            name='review_status',
            field=models.CharField(
                choices=[('pending', '待審核'), ('approved', '已通過'), ('rejected', '已退回')],
                default='pending',
                db_index=True,
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name='viewpointnode',
            name='reviewed_by',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='reviewed_viewpoints',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='viewpointnode',
            name='reviewed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='viewpointnode',
            name='review_notes',
            field=models.TextField(blank=True),
        ),
        migrations.AddIndex(
            model_name='viewpointnode',
            index=models.Index(fields=['review_status'], name='summary_vie_review__64bf61_idx'),
        ),
    ]
