# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import contextlib
import hashlib

from recipe_engine import recipe_api


class TryserverApi(recipe_api.RecipeApi):
  def __init__(self, *args, **kwargs):
    super(TryserverApi, self).__init__(*args, **kwargs)
    self._failure_reasons = []

    self._gerrit_change = None  # self.m.buildbucket.common_pb2.GerritChange
    self._gerrit_change_repo_url = None
    self._gerrit_change_info = None

  def initialize(self):
    changes = self.m.buildbucket.build.input.gerrit_changes
    if len(changes) == 1:
      cl = changes[0]
      self._gerrit_change = cl
      git_host = cl.host
      gs_suffix = '-review.googlesource.com'
      if git_host.endswith(gs_suffix):
        git_host = '%s.googlesource.com' % git_host[:-len(gs_suffix)]
      self._gerrit_change_repo_url = 'https://%s/%s' % (git_host, cl.project)

  @property
  def gerrit_change(self):
    """Returns current gerrit change, if there is exactly one.

    Returns a self.m.buildbucket.common_pb2.GerritChange or None.
    """
    return self._gerrit_change

  @property
  def gerrit_change_repo_url(self):
    """Returns canonical URL of the gitiles repo of the current Gerrit CL.

    Populated iff gerrit_change is populated.
    """
    return self._gerrit_change_repo_url

  @property
  def gerrit_change_info(self):
    """Returns extra info about gerrit_changed, fetched from Gerrit server.

    Populated iff gerrit_change is populated.
    May emit a step when read for the first time.

    Returns:
      A dict representing
      https://gerrit-review.googlesource.com/Documentation/rest-api-changes.html#change-info
    """
    cl = self.gerrit_change
    if cl and self._gerrit_change_info is None:
      o_params = ['ALL_REVISIONS', 'DOWNLOAD_COMMANDS']
      step_test_data = self.m.gerrit.test_api.get_one_change_response_data(
          project=cl.project,
          change=cl.change,
          patchset=cl.patchset,
          # This list must remain static (not dynamically customized by a param)
          # If you need extra info, either change it here (hardcoded) or
          # fetch separetly.
          # If changed, update the docstring above.
          o_params=o_params)
      self._gerrit_change_info = self.m.gerrit.get_changes(
          host='https://' + cl.host,
          query_params=[('change', cl.change)],
          o_params=o_params,
          limit=1,
          name='fetch current CL info',
          step_test_data=lambda: step_test_data)[0]
    return self._gerrit_change_info

  @property
  def gerrit_change_ref(self):
    """Returns gerrit change ref, e.g. "refs/heads/45/12345/6, or None.

    Populated iff gerrit_change is populated.
    """
    info = self.gerrit_change_info
    if not info:  # pragma: no cover
      return None
    # Example of info:
    # {
    #    ... other properties ...
    #   "current_revision": "1c9303c2acf8627286ee998a4437616a1916a6a2",
    #   "revisions": {
    #     "1c9303c2acf8627286ee998a4437616a1916a6a2": {
    #       "kind": "REWORK",
    #       "_number": 6,
    #       "created": "2018-09-07 01:13:45.000000000",
    #       "uploader": {
    #         "_account_id": 1114299
    #       },
    #       "ref": "refs/changes/68/1208368/6",
    #       "fetch": {
    #         "repo": {
    #           "url": "chromium/tools/depot_tools",
    #           "ref": "refs/changes/68/1208368/6",
    #           "commands": {
    #             "repo": "repo download chromium/tools/depot_tools 1208368/6"
    #           }
    #         },
    #         "http": {
    #           "url": "https://chromium.googlesource.com/chromium/tools/depot_tools",
    #           "ref": "refs/changes/68/1208368/6",
    #           "commands": {
    #             "Checkout": "git fetch https://chromium.googlesource.com/chromium/tools/depot_tools refs/changes/68/1208368/6 \u0026\u0026 git checkout FETCH_HEAD",
    #             "Cherry Pick": "git fetch https://chromium.googlesource.com/chromium/tools/depot_tools refs/changes/68/1208368/6 \u0026\u0026 git cherry-pick FETCH_HEAD",
    #             "Pull": "git pull https://chromium.googlesource.com/chromium/tools/depot_tools refs/changes/68/1208368/6"
    #           }
    #         }
    #       },
    #       "description": "rebase"
    #     }
    #   }
    # }
    return info['revisions'][info['current_revision']]['ref']

  @property
  def is_tryserver(self):
    """Returns true iff we have a change to check out."""
    return (self.is_patch_in_git or self.is_gerrit_issue)

  @property
  def is_gerrit_issue(self):
    """Returns true iff the properties exist to match a Gerrit issue."""
    if self.gerrit_change:
      return True
    # TODO(tandrii): remove this, once nobody is using buildbot Gerrit Poller.
    return ('event.patchSet.ref' in self.m.properties and
            'event.change.url' in self.m.properties and
            'event.change.id' in self.m.properties)

  @property
  def is_patch_in_git(self):
    return (self.m.properties.get('patch_storage') == 'git' and
            self.m.properties.get('patch_repo_url') and
            self.m.properties.get('patch_ref'))

  def get_files_affected_by_patch(self, patch_root, **kwargs):
    """Returns list of paths to files affected by the patch.

    Argument:
      patch_root: path relative to api.path['root'], usually obtained from
        api.gclient.calculate_patch_root(patch_project)

    Returned paths will be relative to to patch_root.
    """
    cwd = self.m.context.cwd or self.m.path['start_dir'].join(patch_root)
    with self.m.context(cwd=cwd):
      step_result = self.m.git(
          '-c', 'core.quotePath=false', 'diff', '--cached', '--name-only',
          name='git diff to analyze patch',
          stdout=self.m.raw_io.output(),
          step_test_data=lambda:
            self.m.raw_io.test_api.stream_output('foo.cc'),
          **kwargs)
    paths = [self.m.path.join(patch_root, p) for p in
             step_result.stdout.split()]
    if self.m.platform.is_win:
      # Looks like "analyze" wants POSIX slashes even on Windows (since git
      # uses that format even on Windows).
      paths = [path.replace('\\', '/') for path in paths]
    step_result.presentation.logs['files'] = paths
    return paths

  def set_subproject_tag(self, subproject_tag):
    """Adds a subproject tag to the build.

    This can be used to distinguish between builds that execute different steps
    depending on what was patched, e.g. blink vs. pure chromium patches.
    """
    assert self.is_tryserver

    step_result = self.m.step.active_result
    step_result.presentation.properties['subproject_tag'] = subproject_tag

  def _set_failure_type(self, failure_type):
    if not self.is_tryserver:
      return

    step_result = self.m.step.active_result
    step_result.presentation.properties['failure_type'] = failure_type

  def set_patch_failure_tryjob_result(self):
    """Mark the tryjob result as failure to apply the patch."""
    self._set_failure_type('PATCH_FAILURE')

  def set_compile_failure_tryjob_result(self):
    """Mark the tryjob result as a compile failure."""
    self._set_failure_type('COMPILE_FAILURE')

  def set_test_failure_tryjob_result(self):
    """Mark the tryjob result as a test failure.

    This means we started running actual tests (not prerequisite steps
    like checkout or compile), and some of these tests have failed.
    """
    self._set_failure_type('TEST_FAILURE')

  def set_invalid_test_results_tryjob_result(self):
    """Mark the tryjob result as having invalid test results.

    This means we run some tests, but the results were not valid
    (e.g. no list of specific test cases that failed, or too many
    tests failing, etc).
    """
    self._set_failure_type('INVALID_TEST_RESULTS')

  def add_failure_reason(self, reason):
    """
    Records a more detailed reason why build is failing.

    The reason can be any JSON-serializable object.
    """
    assert self.m.json.is_serializable(reason)
    self._failure_reasons.append(reason)

  @contextlib.contextmanager
  def set_failure_hash(self):
    """
    Context manager that sets a failure_hash build property on StepFailure.

    This can be used to easily compare whether two builds have failed
    for the same reason. For example, if a patch is bad (breaks something),
    we'd expect it to always break in the same way. Different failures
    for the same patch are usually a sign of flakiness.
    """
    try:
      yield
    except self.m.step.StepFailure as e:
      self.add_failure_reason(e.reason)

      try:
        step_result = self.m.step.active_result
      except ValueError:
        step_result = None
      if step_result:
        failure_hash = hashlib.sha1()
        failure_hash.update(self.m.json.dumps(self._failure_reasons))
        step_result.presentation.properties['failure_hash'] = (
            failure_hash.hexdigest())

      raise e

  def get_footers(self, patch_text=None):
    """Retrieves footers from the patch description.

    footers are machine readable tags embedded in commit messages. See
    git-footers documentation for more information.
    """
    if patch_text is None:
      if self.gerrit_change:
        patch_text = self.m.gerrit.get_change_description(
            'https://%s' % self.gerrit_change.host,
            int(self.gerrit_change.change),
            int(self.gerrit_change.patchset))

    result = self.m.python(
        'parse description', self.package_repo_resource('git_footers.py'),
        args=['--json', self.m.json.output()],
        stdin=self.m.raw_io.input(data=patch_text))
    return result.json.output

  def get_footer(self, tag, patch_text=None):
    """Gets a specific tag from a CL description"""
    return self.get_footers(patch_text).get(tag, [])

  def normalize_footer_name(self, footer):
    return '-'.join([ word.title() for word in footer.strip().split('-') ])
