from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0025_aiconversation_ai_turn_is_question_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='postdialogueresponse',
            name='exp_comprehension_1',
            field=models.PositiveSmallIntegerField(
                default=4,
                help_text='1–7 Likert scale',
                verbose_name='C2-7 議題理解程度',
            ),
            preserve_default=False,
        ),
    ]
