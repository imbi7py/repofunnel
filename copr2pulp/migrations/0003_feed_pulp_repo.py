# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('copr2pulp', '0002_auto_20150915_0922'),
    ]

    operations = [
        migrations.AddField(
            model_name='feed',
            name='pulp_repo',
            field=models.CharField(max_length=35, null=True),
        ),
    ]
