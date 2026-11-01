from django.db import migrations


def add_empty_asset_links(apps, schema_editor):
    ChangeRequest = apps.get_model("planner", "WorkOrderChangeRequest")
    for request in ChangeRequest.objects.all().iterator(chunk_size=500):
        proposed = dict(request.proposed_values)
        original = dict(request.original_values)
        if "assets" not in proposed or "assets" not in original:
            proposed.setdefault("assets", [])
            original.setdefault("assets", [])
            request.proposed_values = proposed
            request.original_values = original
            request.save(update_fields=["proposed_values", "original_values"])


def remove_empty_asset_links(apps, schema_editor):
    ChangeRequest = apps.get_model("planner", "WorkOrderChangeRequest")
    for request in ChangeRequest.objects.all().iterator(chunk_size=500):
        proposed = dict(request.proposed_values)
        original = dict(request.original_values)
        proposed.pop("assets", None)
        original.pop("assets", None)
        request.proposed_values = proposed
        request.original_values = original
        request.save(update_fields=["proposed_values", "original_values"])


class Migration(migrations.Migration):
    dependencies = [
        ("planner", "0005_workorder_assets_alter_workorder_equipment"),
    ]

    operations = [
        migrations.RunPython(add_empty_asset_links, remove_empty_asset_links),
    ]
