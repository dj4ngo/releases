# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""Try to verify that the latest commit contains valid SHA values.

"""

from __future__ import print_function

import argparse
import atexit
import glob
import os
import re
import shutil
import tempfile

import requests
import yaml

# Disable warnings about insecure connections.
from requests.packages import urllib3

from openstack_releases import defaults
from openstack_releases import gitutils
from openstack_releases import versionutils

urllib3.disable_warnings()


def is_a_hash(val):
    "Return bool indicating if val looks like a valid hash."
    return re.search('^[a-f0-9]{40}$', val, re.I) is not None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--no-cleanup',
        dest='cleanup',
        default=True,
        action='store_false',
        help='do not remove temporary files',
    )
    parser.add_argument(
        'input',
        nargs='*',
        help=('YAML files to validate, defaults to '
              'files changed in the latest commit'),
    )
    args = parser.parse_args()

    filenames = args.input or gitutils.find_modified_deliverable_files()
    if not filenames:
        print('no modified deliverable files, validating all releases from %s'
              % defaults.RELEASE)
        filenames = glob.glob('deliverables/' + defaults.RELEASE + '/*.yaml')

    errors = []

    workdir = tempfile.mkdtemp(prefix='releases-')
    print('creating temporary files in %s' % workdir)

    def cleanup_workdir():
        if args.cleanup:
            try:
                shutil.rmtree(workdir)
            except:
                pass
        else:
            print('not cleaning up %s' % workdir)
    atexit.register(cleanup_workdir)

    for filename in filenames:
        print('\nChecking %s' % filename)
        if not os.path.isfile(filename):
            print("File was deleted, skipping.")
            continue
        with open(filename, 'r') as f:
            deliverable_info = yaml.load(f.read())

        # Look for the launchpad project
        try:
            lp_name = deliverable_info['launchpad']
        except KeyError:
            errors.append('No launchpad project given in %s' % filename)
            print('no launchpad project name given')
        else:
            print('launchpad project %s ' % lp_name, end='')
            lp_resp = requests.get('https://api.launchpad.net/1.0/' + lp_name)
            if (lp_resp.status_code // 100) == 4:
                print('MISSING')
                errors.append('Launchpad project %s does not exist' % lp_name)
            else:
                print('found')

        # Look for an email address to receive release announcements
        try:
            announce_to = deliverable_info['send-announcements-to']
        except KeyError:
            errors.append('No send-announcements-to in %s'
                          % filename)
            print('no send-announcements-to found')
        else:
            print('send announcements to %s' % announce_to)
            if ' ' in announce_to:
                print('Found space in send-announcements-to: %r' %
                      announce_to)
                errors.append('Space in send-announcements-to (%r) for %s' %
                              (announce_to, filename))

        prev_version = None
        prev_projects = set()
        for release in deliverable_info['releases']:

            for e in versionutils.validate_version(release['version']):
                print(e)
                errors.append(e)

            for project in release['projects']:
                print('%s SHA %s ' % (project['repo'],
                                      project['hash']),
                      end='')

                if not is_a_hash(project['hash']):
                    print('NOT A SHA HASH')
                    errors.append(
                        ('%(repo)s version %(version)s release from '
                         '%(hash)r, which is not a hash') % {
                             'repo': project['repo'],
                             'hash': project['hash'],
                             'version': release['version'],
                             }
                    )
                else:
                    # Report if the SHA exists or not (an error if it
                    # does not).
                    sha_exists = gitutils.commit_exists(
                        project['repo'], project['hash'],
                    )
                    if not sha_exists:
                        print('MISSING', end='')
                        errors.append('No commit %(hash)r in %(repo)r'
                                      % project)
                    else:
                        print('found ', end='')
                    # Report if the version has already been
                    # tagged. We expect it to not exist, but neither
                    # case is an error because sometimes we want to
                    # import history and sometimes we want to make new
                    # releases.
                    print('version %s ' % release['version'], end='')
                    version_exists = gitutils.commit_exists(
                        project['repo'], release['version'],
                    )
                    gitutils.clone_repo(workdir, project['repo'])
                    if version_exists:
                        actual_sha = gitutils.sha_for_tag(
                            workdir,
                            project['repo'],
                            release['version'],
                        )
                        if actual_sha == project['hash']:
                            print('found and matches SHA')
                        else:
                            print('found DIFFERENT %r' % actual_sha)
                            errors.append(
                                ('Version %s in %s is on '
                                 'commit %s instead of %s') %
                                (release['version'],
                                 project['repo'],
                                 actual_sha,
                                 project['hash']))
                    else:
                        print('NEW ', end='')
                        if not prev_version:
                            print()
                        elif project['repo'] not in prev_projects:
                            print('not included in previous release for %s: %s' %
                                  (prev_version, ', '.join(sorted(prev_projects))))
                        else:
                            # Check to see if the commit for the new
                            # version is in the ancestors of the
                            # previous release, meaning it is actually
                            # merged into the branch.
                            is_ancestor = gitutils.check_ancestry(
                                workdir,
                                project['repo'],
                                prev_version,
                                project['hash'],
                            )
                            if is_ancestor:
                                print('SHA found in descendants')
                            else:
                                print('SHA NOT FOUND in descendants')
                                errors.append(
                                    '%s %s is not a descendant of %s' % (
                                        project['repo'], project['hash'],
                                        prev_version)
                                )
            prev_version = release['version']
            prev_projects = set(p['repo'] for p in release['projects'])

    if errors:
        print('\n%s errors found' % len(errors))
        for e in errors:
            print(e)

    return 1 if errors else 0
