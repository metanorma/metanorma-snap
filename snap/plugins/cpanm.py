import snapcraft

import os
import shutil
import subprocess
import urllib.request

class CpanmPlugin(snapcraft.BasePlugin):

    @classmethod
    def schema(cls):
        schema = super().schema()

        schema['properties']['cpanm-packages'] = {
            'type': 'array',
        }
        schema['properties']['run-test'] = {
            'type': 'boolean',
            'default': False
        }

        schema['required'] = ['cpanm-packages']

        return schema

    def __init__(self, name, options, project):
        super().__init__(name, options, project)

        self.stage_packages.append('perl')

        self._install_cmd = ['cpanm']

        if self.options.run_test == False:
            self._install_cmd.append('--notest')

        self._install_cmd.append(' '.join(self.options.cpanm_packages))

    def pull(self):
        super().pull()
        cpanm_pl = os.path.join(self.sourcedir, 'cpanminus.pl')
        urllib.request.urlretrieve('http://cpanmin.us', cpanm_pl)

        self.run(['perl', '--', cpanm_pl, 'App::cpanminus'])

    def build(self):
        super().build()

        include_search_paths = [os.path.join(self.installdir, 'usr', 'include'), os.path.join(self.installdir, 'usr', 'include', self.run_output(['gcc', '-dumpmachine']))]

        env_with_cpath = os.environ.copy()
        env_with_cpath['CPATH'] = ':'.join(include_search_paths)

        self.run(self._install_cmd, env=env_with_cpath)

        # cpanm install modules to `/usr/local` but default
        # unfortunatelly no best solution was found
        shutil.copytree('/usr/local', os.path.join(self.installdir, 'local'))