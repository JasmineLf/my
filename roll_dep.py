#!/usr/bin/env python3
# Copyright 2015 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Rolls DEPS controlled dependency.

Works only with git checkout and git dependencies. Currently this script will
always roll to the tip of to origin/main.
"""

from __future__ import print_function

import argparse
import itertools
import os
import re
import subprocess2
import sys
import tempfile

NEED_SHELL = sys.platform.startswith('win')
GCLIENT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'gclient.py')


# Commit subject that will be considered a roll. In the format generated by the
# git log used, so it's "<year>-<month>-<day> <author> <subject>"
_ROLL_SUBJECT = re.compile(
    # Date
    r'^\d\d\d\d-\d\d-\d\d '
    # Author
    r'[^ ]+ '
    # Subject
    r'('
      # Generated by
      # https://skia.googlesource.com/buildbot/+/HEAdA/autoroll/go/repo_manager/deps_repo_manager.go
      r'Roll [^ ]+ [a-f0-9]+\.\.[a-f0-9]+ \(\d+ commits\)'
      r'|'
      # Generated by
      # https://chromium.googlesource.com/infra/infra/+/HEAD/recipes/recipe_modules/recipe_autoroller/api.py
      r'Roll recipe dependencies \(trivial\)\.'
    r')$')


class Error(Exception):
  pass


class AlreadyRolledError(Error):
  pass


def check_output(*args, **kwargs):
  """subprocess2.check_output() passing shell=True on Windows for git."""
  kwargs.setdefault('shell', NEED_SHELL)
  return subprocess2.check_output(*args, **kwargs).decode('utf-8')


def check_call(*args, **kwargs):
  """subprocess2.check_call() passing shell=True on Windows for git."""
  kwargs.setdefault('shell', NEED_SHELL)
  subprocess2.check_call(*args, **kwargs)


def return_code(*args, **kwargs):
  """subprocess2.call() passing shell=True on Windows for git and
  subprocess2.DEVNULL for stdout and stderr."""
  kwargs.setdefault('shell', NEED_SHELL)
  kwargs.setdefault('stdout', subprocess2.DEVNULL)
  kwargs.setdefault('stderr', subprocess2.DEVNULL)
  return subprocess2.call(*args, **kwargs)


def is_pristine(root):
  """Returns True if a git checkout is pristine."""
  # `git rev-parse --verify` has a non-zero return code if the revision
  # doesn't exist.
  diff_cmd = ['git', 'diff', '--ignore-submodules', 'origin/main']
  return (not check_output(diff_cmd, cwd=root).strip() and
          not check_output(diff_cmd + ['--cached'], cwd=root).strip())



def get_log_url(upstream_url, head, tot):
  """Returns an URL to read logs via a Web UI if applicable."""
  if re.match(r'https://[^/]*\.googlesource\.com/', upstream_url):
    # gitiles
    return '%s/+log/%s..%s' % (upstream_url, head[:12], tot[:12])
  if upstream_url.startswith('https://github.com/'):
    upstream_url = upstream_url.rstrip('/')
    if upstream_url.endswith('.git'):
      upstream_url = upstream_url[:-len('.git')]
    return '%s/compare/%s...%s' % (upstream_url, head[:12], tot[:12])
  return None


def should_show_log(upstream_url):
  """Returns True if a short log should be included in the tree."""
  # Skip logs for very active projects.
  if upstream_url.endswith('/v8/v8.git'):
    return False
  if 'webrtc' in upstream_url:
    return False
  return True


def gclient(args):
  """Executes gclient with the given args and returns the stdout."""
  return check_output([sys.executable, GCLIENT_PATH] + args).strip()


def generate_commit_message(
    full_dir, dependency, head, roll_to, no_log, log_limit):
  """Creates the commit message for this specific roll."""
  commit_range = '%s..%s' % (head, roll_to)
  commit_range_for_header = '%s..%s' % (head[:9], roll_to[:9])
  upstream_url = check_output(
      ['git', 'config', 'remote.origin.url'], cwd=full_dir).strip()
  log_url = get_log_url(upstream_url, head, roll_to)
  cmd = ['git', 'log', commit_range, '--date=short', '--no-merges']
  logs = check_output(
      # Args with '=' are automatically quoted.
      cmd + ['--format=%ad %ae %s', '--'],
      cwd=full_dir).rstrip()
  logs = re.sub(r'(?m)^(\d\d\d\d-\d\d-\d\d [^@]+)@[^ ]+( .*)$', r'\1\2', logs)
  lines = logs.splitlines()
  cleaned_lines = [l for l in lines if not _ROLL_SUBJECT.match(l)]
  logs = '\n'.join(cleaned_lines) + '\n'

  nb_commits = len(lines)
  rolls = nb_commits - len(cleaned_lines)
  header = 'Roll %s/ %s (%d commit%s%s)\n\n' % (
      dependency,
      commit_range_for_header,
      nb_commits,
      's' if nb_commits > 1 else '',
      ('; %s trivial rolls' % rolls) if rolls else '')
  log_section = ''
  if log_url:
    log_section = log_url + '\n\n'
  log_section += '$ %s ' % ' '.join(cmd)
  log_section += '--format=\'%ad %ae %s\'\n'
  log_section = log_section.replace(commit_range, commit_range_for_header)
  # It is important that --no-log continues to work, as it is used by
  # internal -> external rollers. Please do not remove or break it.
  if not no_log and should_show_log(upstream_url):
    if len(cleaned_lines) > log_limit:
      # Keep the first N/2 log entries and last N/2 entries.
      lines = logs.splitlines(True)
      lines = lines[:log_limit//2] + ['(...)\n'] + lines[-log_limit//2:]
      logs = ''.join(lines)
    log_section += logs
  return header + log_section


def is_submoduled():
  """Returns true if gclient root has submodules"""
  return os.path.isfile(os.path.join(gclient(['root']), ".gitmodules"))


def get_submodule_rev(submodule_path):
  """Returns revision of the given submodule path"""
  rev = check_output(['git', 'submodule', 'status', submodule])

  # git submodule status <path> returns all submodules with its rev in the
  # pattern: `(+|-)(<revision>) (submodule.path)`
  return rev.split(' ')[0][1:]


def calculate_roll(full_dir, dependency, roll_to):
  """Calculates the roll for a dependency by processing gclient_dict, and
  fetching the dependency via git.
  """
  # if the super-project uses submodules, get rev directly using git.
  if is_submoduled():
    head = get_submodule_rev(submodule_path)
  else:
    head = gclient(['getdep', '-r', dependency])
  if not head:
    raise Error('%s is unpinned.' % dependency)
  check_call(['git', 'fetch', 'origin', '--quiet'], cwd=full_dir)
  if roll_to == 'origin/HEAD':
    check_output(['git', 'remote', 'set-head', 'origin', '-a'], cwd=full_dir)

  roll_to = check_output(['git', 'rev-parse', roll_to], cwd=full_dir).strip()
  return head, roll_to



def gen_commit_msg(logs, cmdline, reviewers, bug):
  """Returns the final commit message."""
  commit_msg = ''
  if len(logs) > 1:
    commit_msg = 'Rolling %d dependencies\n\n' % len(logs)
  commit_msg += '\n\n'.join(logs)
  commit_msg += '\nCreated with:\n  ' + cmdline + '\n'
  commit_msg += 'R=%s\n' % ','.join(reviewers) if reviewers else ''
  commit_msg += '\nBug: %s\n' % bug if bug else ''
  return commit_msg


def finalize(commit_msg, current_dir, rolls):
  """Commits changes to the DEPS file, then uploads a CL."""
  print('Commit message:')
  print('\n'.join('    ' + i for i in commit_msg.splitlines()))

  # Pull the dependency to the right revision. This is surprising to users
  # otherwise. The revision update is done before commiting to update
  # submodule revision if present.
  for _head, roll_to, full_dir in sorted(rolls.values()):
    check_call(['git', 'checkout', '--quiet', roll_to], cwd=full_dir)

  check_call(['git', 'add', 'DEPS'], cwd=current_dir)
  # We have to set delete=False and then let the object go out of scope so
  # that the file can be opened by name on Windows.
  with tempfile.NamedTemporaryFile('w+', newline='', delete=False) as f:
    commit_filename = f.name
    f.write(commit_msg)
  check_call(['git', 'commit', '--quiet', '--file', commit_filename],
             cwd=current_dir)
  os.remove(commit_filename)


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument(
      '--ignore-dirty-tree', action='store_true',
      help='Roll anyways, even if there is a diff.')
  parser.add_argument(
      '-r',
      '--reviewer',
      action='append',
      help=
      'To specify multiple reviewers, either use a comma separated list, e.g. '
      '-r joe,jane,john or provide the flag multiple times, e.g. '
      '-r joe -r jane. Defaults to @chromium.org')
  parser.add_argument('-b', '--bug', help='Associate a bug number to the roll')
  # It is important that --no-log continues to work, as it is used by
  # internal -> external rollers. Please do not remove or break it.
  parser.add_argument(
      '--no-log', action='store_true',
      help='Do not include the short log in the commit message')
  parser.add_argument(
      '--log-limit', type=int, default=100,
      help='Trim log after N commits (default: %(default)s)')
  parser.add_argument(
      '--roll-to', default='origin/HEAD',
      help='Specify the new commit to roll to (default: %(default)s)')
  parser.add_argument(
      '--key', action='append', default=[],
      help='Regex(es) for dependency in DEPS file')
  parser.add_argument('dep_path', nargs='+', help='Path(s) to dependency')
  args = parser.parse_args()

  if len(args.dep_path) > 1:
    if args.roll_to != 'origin/HEAD':
      parser.error(
          'Can\'t use multiple paths to roll simultaneously and --roll-to')
    if args.key:
      parser.error(
          'Can\'t use multiple paths to roll simultaneously and --key')
  reviewers = None
  if args.reviewer:
    reviewers = list(itertools.chain(*[r.split(',') for r in args.reviewer]))
    for i, r in enumerate(reviewers):
      if not '@' in r:
        reviewers[i] = r + '@chromium.org'

  gclient_root = gclient(['root'])
  current_dir = os.getcwd()
  dependencies = sorted(d.replace('\\', '/').rstrip('/') for d in args.dep_path)
  cmdline = 'roll-dep ' + ' '.join(dependencies) + ''.join(
      ' --key ' + k for k in args.key)
  try:
    if not args.ignore_dirty_tree and not is_pristine(current_dir):
      raise Error(
          'Ensure %s is clean first (no non-merged commits).' % current_dir)
    # First gather all the information without modifying anything, except for a
    # git fetch.
    rolls = {}
    for dependency in dependencies:
      full_dir = os.path.normpath(os.path.join(gclient_root, dependency))
      if not os.path.isdir(full_dir):
        print('Dependency %s not found at %s' % (dependency, full_dir))
        full_dir = os.path.normpath(os.path.join(current_dir, dependency))
        print('Will look for relative dependency at %s' % full_dir)
        if not os.path.isdir(full_dir):
          raise Error('Directory not found: %s (%s)' % (dependency, full_dir))

      head, roll_to = calculate_roll(full_dir, dependency, args.roll_to)
      if roll_to == head:
        if len(dependencies) == 1:
          raise AlreadyRolledError('No revision to roll!')
        print('%s: Already at latest commit %s' % (dependency, roll_to))
      else:
        print(
            '%s: Rolling from %s to %s' % (dependency, head[:10], roll_to[:10]))
        rolls[dependency] = (head, roll_to, full_dir)

    logs = []
    setdep_args = []
    for dependency, (head, roll_to, full_dir) in sorted(rolls.items()):
      log = generate_commit_message(
          full_dir, dependency, head, roll_to, args.no_log, args.log_limit)
      logs.append(log)
      setdep_args.extend(['-r', '{}@{}'.format(dependency, roll_to)])

    # DEPS is updated even if the repository uses submodules.
    gclient(['setdep'] + setdep_args)

    commit_msg = gen_commit_msg(logs, cmdline, reviewers, args.bug)
    finalize(commit_msg, current_dir, rolls)
  except Error as e:
    sys.stderr.write('error: %s\n' % e)
    return 2 if isinstance(e, AlreadyRolledError) else 1
  except subprocess2.CalledProcessError:
    return 1

  print('')
  if not reviewers:
    print('You forgot to pass -r, make sure to insert a R=foo@example.com line')
    print('to the commit description before emailing.')
    print('')
  print('Run:')
  print('  git cl upload --send-mail')
  return 0


if __name__ == '__main__':
  sys.exit(main())
