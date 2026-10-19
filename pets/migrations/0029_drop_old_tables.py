"""Drop the old tracking tables an earlier version kept (PUP-145), and forget their content types, permissions,
groups, settings and migration records. No Housetrained table refers to them."""
from django.db import migrations

APPS = ("core", "dashboard", "reports", "api")


def drop_old_tables(apps, schema_editor):
    connection = schema_editor.connection
    tables = [t for t in connection.introspection.table_names() if t.startswith("core_")]
    cascade = " CASCADE" if connection.vendor == "postgresql" else ""
    with connection.cursor() as cursor:
        for table in tables:
            cursor.execute(f"DROP TABLE IF EXISTS {connection.ops.quote_name(table)}{cascade}")
        cursor.execute("DELETE FROM django_migrations WHERE app IN (%s, %s, %s, %s)", APPS)
        if "dbsettings_setting" in connection.introspection.table_names():
            cursor.execute("DELETE FROM dbsettings_setting WHERE module_name = %s", ["core.models"])
    apps.get_model("contenttypes", "ContentType").objects.filter(app_label__in=APPS).delete()  # and their permissions
    apps.get_model("auth", "Group").objects.filter(name__in=["read_only", "caregiver"]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("pets", "0028_units_per_kind"),
        ("contenttypes", "0002_remove_content_type_name"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [migrations.RunPython(drop_old_tables, migrations.RunPython.noop)]
