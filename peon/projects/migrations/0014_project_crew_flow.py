from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("projects", "0013_agent_message_checkpoint"),
    ]

    operations = [
        migrations.AddField(
            model_name="project",
            name="crew_flow_id",
            field=models.CharField(
                blank=True,
                default="",
                help_text="CrewAI project Flow / crew run id (Peon-crewAI).",
                max_length=64,
            ),
        ),
        migrations.AddField(
            model_name="project",
            name="crew_status",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Crew run status: running|paused|awaiting_feedback|stopped|done.",
                max_length=32,
            ),
        ),
    ]
