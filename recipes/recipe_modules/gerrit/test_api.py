# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import recipe_test_api


class GerritTestApi(recipe_test_api.RecipeTestApi):

  def _make_gerrit_response_json(self, data):
    return self.m.json.output(data)

  def make_gerrit_create_branch_response_data(self):
    return self._make_gerrit_response_json({
      "ref": "refs/heads/test",
      "revision": "76016386a0d8ecc7b6be212424978bb45959d668",
      "can_delete": True
    })

  def make_gerrit_get_branch_response_data(self):
    return self._make_gerrit_response_json({
      "ref": "refs/heads/master",
      "revision": "67ebf73496383c6777035e374d2d664009e2aa5c"
    })

  def get_branch_response_data(self):
    return self._make_gerrit_response_json("master")

  def get_changes_response_data(self):
    # Exemplary list of changes. Note: This contains only a subset of the
    # key/value pairs present in production to limit recipe simulation output.
    return self._make_gerrit_response_json([
      {
        'status': 'NEW',
        'created': '2017-01-30 13:11:20.000000000',
        '_number': '91827',
        'change_id': 'Ideadbeef',
        'project': 'chromium/src',
        'has_review_started': False,
        'branch': 'master',
        'subject': 'Change title',
      },
    ])
