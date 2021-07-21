# Copyright 2013 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import recipe_api

class GerritApi(recipe_api.RecipeApi):
  """Module for interact with Gerrit endpoints"""

  def __init__(self, *args, **kwargs):
    super(GerritApi, self).__init__(*args, **kwargs)
    self._changes_target_branch_cache = {}

  def __call__(self, name, cmd, infra_step=True, **kwargs):
    """Wrapper for easy calling of gerrit_utils steps."""
    assert isinstance(cmd, (list, tuple))
    prefix = 'gerrit '

    env = self.m.context.env
    env.setdefault('PATH', '%(PATH)s')
    env['PATH'] = self.m.path.pathsep.join([
        env['PATH'], str(self.repo_resource())])

    with self.m.context(env=env):
      return self.m.python(prefix + name,
                           self.repo_resource('gerrit_client.py'),
                           cmd,
                           infra_step=infra_step,
                           venv=True,
                           **kwargs)

  def change_edit(self, host, change, path, file, **kwargs):
    """Puts content of a file into a change edit."""
    args = [
        'changeedit',
        '--host',
        host,
        '--change',
        change,
        '--path',
        path,
        '--file',
        file,
    ]
    step_name = 'edit file %s' % path
    step_result = self(step_name, args, **kwargs)
    return step_result

  def publish_edit(self, host, change, **kwargs):
    """Publish a Gerrit change edit."""
    args = [
        'publishchangeedit',
        '--host',
        host,
        '--change',
        change,
    ]
    step_result = self('publish edit', args, **kwargs)
    return step_result

  def create_change(self, host, project, branch, commit_msg, **kwargs):
    """Creates a new gerrit change on the given project and branch

    Returns:
      The change number in str.
    """
    args = [
        'createchange',
        '--host',
        host,
        '--project',
        project,
        '--branch',
        branch,
        '--subject',
        commit_msg,
        '--json_file',
        self.m.json.output(),
    ]
    step_name = 'create change at (%s %s)' % (project, branch)
    step_result = self(step_name, args, **kwargs)
    return step_result.json.output.get('_number')

  def submit_change(self, host, change, **kwargs):
    """Submit a gerrit change"""
    args = [
        'submitchange',
        '--host',
        host,
        '--change',
        change,
    ]
    step_name = 'Submit change %s' % change
    step_result = self(step_name, args, **kwargs)
    return step_result

  def create_gerrit_branch(self, host, project, branch, commit, **kwargs):
    """Creates a new branch from given project and commit

    Returns:
      The ref of the branch created
    """
    args = [
        'branch',
        '--host', host,
        '--project', project,
        '--branch', branch,
        '--commit', commit,
        '--json_file', self.m.json.output()
    ]
    step_name = 'create_gerrit_branch (%s %s)' % (project, branch)
    step_result = self(step_name, args, **kwargs)
    ref = step_result.json.output.get('ref')
    return ref

  # TODO(machenbach): Rename to get_revision? And maybe above to
  # create_ref?
  def get_gerrit_branch(self, host, project, branch, **kwargs):
    """Gets a branch from given project and commit

    Returns:
      The revision of the branch
    """
    args = [
        'branchinfo',
        '--host', host,
        '--project', project,
        '--branch', branch,
        '--json_file', self.m.json.output()
    ]
    step_name = 'get_gerrit_branch (%s %s)' % (project, branch)
    step_result = self(step_name, args, **kwargs)
    revision = step_result.json.output.get('revision')
    return revision

  def get_change_description(self,
                             host,
                             change,
                             patchset,
                             timeout=None,
                             step_test_data=None):
    """Gets the description for a given CL and patchset.

    Args:
      host: URL of Gerrit host to query.
      change: The change number.
      patchset: The patchset number.

    Returns:
      The description corresponding to given CL and patchset.
    """
    ri = self.get_revision_info(host, change, patchset, timeout, step_test_data)
    return ri['commit']['message']

  def get_revision_info(self,
                        host,
                        change,
                        patchset,
                        timeout=None,
                        step_test_data=None):
    """
    Returns the info for a given patchset of a given change.

    Args:
      host: Gerrit host to query.
      change: The change number.
      patchset: The patchset number.

    Returns:
      A dict for the target revision as documented here:
          https://gerrit-review.googlesource.com/Documentation/rest-api-changes.html#list-changes
    """
    assert int(change), change
    assert int(patchset), patchset

    step_test_data = step_test_data or (
        lambda: self.test_api.get_one_change_response_data(change_number=change,
                                                           patchset=patchset))

    cls = self.get_changes(host,
                           query_params=[('change', str(change))],
                           o_params=['ALL_REVISIONS', 'ALL_COMMITS'],
                           limit=1,
                           timeout=timeout,
                           step_test_data=step_test_data)
    cl = cls[0] if len(cls) == 1 else {'revisions': {}}
    for ri in cl['revisions'].values():
      # TODO(tandrii): add support for patchset=='current'.
      if str(ri['_number']) == str(patchset):
        return ri

    raise self.m.step.InfraFailure(
        'Error querying for CL description: host:%r change:%r; patchset:%r' % (
            host, change, patchset))

  def get_changes(self, host, query_params, start=None, limit=None,
                  o_params=None, step_test_data=None, **kwargs):
    """Queries changes for the given host.

    Args:
      * host: URL of Gerrit host to query.
      * query_params: Query parameters as list of (key, value) tuples to form a
          query as documented here:
          https://gerrit-review.googlesource.com/Documentation/user-search.html#search-operators
      * start: How many changes to skip (starting with the most recent).
      * limit: Maximum number of results to return.
      * o_params: A list of additional output specifiers, as documented here:
          https://gerrit-review.googlesource.com/Documentation/rest-api-changes.html#list-changes
      * step_test_data: Optional mock test data for the underlying gerrit client.

    Returns:
      A list of change dicts as documented here:
          https://gerrit-review.googlesource.com/Documentation/rest-api-changes.html#list-changes
    """
    args = [
        'changes',
        '--host', host,
        '--json_file', self.m.json.output()
    ]
    if start:
      args += ['--start', str(start)]
    if limit:
      args += ['--limit', str(limit)]
    for k, v in query_params:
      args += ['-p', '%s=%s' % (k, v)]
    for v in (o_params or []):
      args += ['-o', v]
    if not step_test_data:
      step_test_data = lambda: self.test_api.get_one_change_response_data()

    return self(
        kwargs.pop('name', 'changes'),
        args,
        step_test_data=step_test_data,
        **kwargs
    ).json.output

  def get_related_changes(self, host, change, revision='current', step_test_data=None):
    """Queries related changes for a given host, change, and revision.

    Args:
      * host: URL of Gerrit host to query.
      * change: The change-id of the change to get related changes for as
          documented here:
          https://gerrit-review.googlesource.com/Documentation/rest-api-changes.html#change-id
      * revision: The revision-id of the revision to get related changes for as
          documented here:
          https://gerrit-review.googlesource.com/Documentation/rest-api-changes.html#revision-id
          This defaults to current, which names the most recent patch set.
      * step_test_data: Optional mock test data for the underlying gerrit client.

    Returns:
      A related changes dictionary as documented here:
          https://gerrit-review.googlesource.com/Documentation/rest-api-changes.html#related-changes-info

    """
    args = [
        'relatedchanges',
        '--host',
        host,
        '--change',
        change,
        '--revision',
        revision,
        '--json_file',
        self.m.json.output(),
    ]
    if not step_test_data:
      step_test_data = lambda: self.test_api.get_related_changes_response_data()

    return self('relatedchanges', args,
                step_test_data=step_test_data).json.output

  def abandon_change(self, host, change, message=None, name=None,
                     step_test_data=None):
    args = [
        'abandon',
        '--host', host,
        '--change', int(change),
        '--json_file', self.m.json.output(),
    ]
    if message:
      args.extend(['--message', message])
    if not step_test_data:
      step_test_data = lambda: self.test_api.get_one_change_response_data(
          status='ABANDONED', _number=str(change))

    return self(
        name or 'abandon',
        args,
        step_test_data=step_test_data,
    ).json.output

  def move_changes(self,
                   host,
                   project,
                   from_branch,
                   to_branch,
                   step_test_data=None):
    args = [
        'movechanges', '--host', host, '-p',
        'project=%s' % project, '-p',
        'branch=%s' % from_branch, '-p', 'status=open', '--destination_branch',
        to_branch, '--json_file',
        self.m.json.output()
    ]

    if not step_test_data:
      step_test_data = lambda: self.test_api.get_one_change_response_data(
          branch=to_branch)

    return self(
        'move changes',
        args,
        step_test_data=step_test_data,
    ).json.output
