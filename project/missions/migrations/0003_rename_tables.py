from django.db import migrations


class Migration(migrations.Migration):
    """Give the scenario and feature tables clearer names.

    ``Scenario``  -> table ``base_location``  (one row per scenario: base, target, grid outline)
    ``ScenarioFeature`` -> table ``rover_hazards`` (one row per hazard or obstacle cell)
    ``Run``       -> table ``rover_route``     (one row per route the rover drove)

    Renaming keeps the data, the foreign keys and the indexes created in 0001 and 0002.
    """

    dependencies = [("missions", "0002_geography_index")]

    operations = [
        migrations.AlterModelTable(name="scenario", table="base_location"),
        migrations.AlterModelTable(name="scenariofeature", table="rover_hazards"),
        migrations.AlterModelTable(name="run", table="rover_route"),
    ]
