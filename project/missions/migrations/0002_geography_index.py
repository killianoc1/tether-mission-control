from django.db import migrations


class Migration(migrations.Migration):
    """Functional GiST indexes on ``geometry::geography`` expressions.

    GeoDjango's automatic GiST indexes are on the ``geometry`` columns.  Two of
    our queries measure in *metres*, so they work on ``geography`` casts:

    * Q5 filters with ``ST_DWithin(base::geography, point, metres)``;
    * Q1 orders with ``geom::geography <-> point`` (k-nearest-neighbour).

    PostgreSQL can only use an index for those expressions if the index is
    built on the very same cast expression, hence these two indexes.
    """

    dependencies = [("missions", "0001_initial")]

    operations = [
        migrations.RunSQL(
            sql="CREATE INDEX scenario_base_geog_gist ON missions_scenario USING GIST ((base::geography));",
            reverse_sql="DROP INDEX IF EXISTS scenario_base_geog_gist;",
        ),
        migrations.RunSQL(
            sql="CREATE INDEX feature_geom_geog_gist ON missions_scenariofeature USING GIST ((geom::geography));",
            reverse_sql="DROP INDEX IF EXISTS feature_geom_geog_gist;",
        ),
    ]
