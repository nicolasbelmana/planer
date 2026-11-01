from django.db import migrations


def rename_technician_role(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    old_group = Group.objects.filter(name="Tecnico").first()
    if old_group is None:
        return
    operator_group, _ = Group.objects.get_or_create(name="Operario")
    through_model = Group.user_set.through
    for relation in through_model.objects.filter(group_id=old_group.pk):
        through_model.objects.get_or_create(
            group_id=operator_group.pk,
            user_id=relation.user_id,
        )
    old_group.delete()


def restore_technician_role(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    operator_group = Group.objects.filter(name="Operario").first()
    if operator_group is None:
        return
    technician_group, _ = Group.objects.get_or_create(name="Tecnico")
    through_model = Group.user_set.through
    for relation in through_model.objects.filter(group_id=operator_group.pk):
        through_model.objects.get_or_create(
            group_id=technician_group.pk,
            user_id=relation.user_id,
        )
    operator_group.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("planner", "0001_initial"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.RunPython(rename_technician_role, restore_technician_role),
    ]
