# -*- Mode:Python; indent-tabs-mode:nil; tab-width:4 -*-
import collections
import contextlib
import json
import os
import shutil
import subprocess
import sys

import snapcraft
from snapcraft import sources
from snapcraft.internal import errors
from snapcraft.file_utils import link_or_copy, link_or_copy_tree


_NODEJS_BASE = "node-v{version}-linux-{arch}"
_NODEJS_VERSION = "8.12.0"
_NODEJS_TMPL = "https://nodejs.org/dist/v{version}/{base}.tar.gz"
_NODEJS_ARCHES = {
    "i386": "x86",
    "amd64": "x64",
    "armhf": "armv7l",
    "arm64": "arm64",
    "ppc64el": "ppc64le",
    "s390x": "s390x",
}

# Insupired by https://github.com/snapcore/snapcraft/blob/master/snapcraft/plugins/nodejs.py
# Probably we will move to it later
class NpmPlugin(snapcraft.BasePlugin):
    @classmethod
    def schema(cls):
        schema = super().schema()

        schema["properties"]["nodejs-version"] = {
            "type": "string",
            "default": _NODEJS_VERSION,
        }
        schema["properties"]["nodejs-packages"] = {"type": "array", "default": []}

        schema["required"] = ["nodejs-packages"]

        return schema

    @classmethod
    def get_pull_properties(cls):
        # Inform Snapcraft of the properties associated with pulling. If these
        # change in the YAML Snapcraft will consider the build step dirty.
        return ["nodejs-version"]

    @classmethod
    def get_build_properties(cls):
        # Inform Snapcraft of the properties associated with pulling. If these
        # change in the YAML Snapcraft will consider the build step dirty.
        return ["nodejs-packages"]

    @property
    def _nodejs_tar(self):
        if self._nodejs_tar_handle is None:
            self._nodejs_tar_handle = sources.Tar(
                self._nodejs_release_uri, self.installdir
            )
        return self._nodejs_tar_handle

    def __init__(self, name, options, project):
        super().__init__(name, options, project)

        self._nodejs_release_uri = get_nodejs_release(
            self.options.nodejs_version, self.project.deb_arch
        )
        self._nodejs_tar_handle = None

    def pull(self):
        super().pull()

        self._nodejs_tar.download()

        self._nodejs_tar.provision(self.installdir, clean_target=False, keep_tarball=True)

        self.run(["npm", "config", "set", "user", "0"], self.installdir)
        self.run(["npm", "config", "set", "unsafe-perm", "true"], self.installdir)

    def build(self):
        super().build()

        self.run(["npm", "install"] + self.options.nodejs_packages, self.installdir)

    def run(self, cmd, rootdir):
        super().run(cmd, cwd=rootdir, env=self._build_environment())

    def _build_environment(self):
        env = os.environ.copy()
        npm_bin = os.path.join(self.installdir, "bin")

        if env.get("PATH"):
            new_path = "{}:{}".format(npm_bin, env.get("PATH"))
        else:
            new_path = npm_bin

        env["PATH"] = new_path
        return env

def _get_nodejs_base(node_engine, machine):
    if machine not in _NODEJS_ARCHES:
        raise errors.SnapcraftEnvironmentError(
            "architecture not supported ({})".format(machine)
        )
    return _NODEJS_BASE.format(version=node_engine, arch=_NODEJS_ARCHES[machine])


def get_nodejs_release(node_engine, arch):
    return _NODEJS_TMPL.format(
        version=node_engine, base=_get_nodejs_base(node_engine, arch)
    )