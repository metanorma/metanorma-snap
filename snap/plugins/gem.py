import glob
import logging
import os
import re

from snapcraft import BasePlugin, file_utils
from snapcraft.internal import errors
from snapcraft.sources import Tar


logger = logging.getLogger(__name__)

# Insupred by https://github.com/snapcore/snapcraft/blob/master/snapcraft/plugins/ruby.py
# Probably we will move to it later
class GemPlugin(BasePlugin):
    @classmethod
    def schema(cls):
        schema = super().schema()

        schema["properties"]["ruby-version"] = {
            "type": "string",
            "default": "2.4.2",
            "pattern": r"^\d+\.\d+(\.\d+)?$",
        }
        schema["required"] = ["source", "source-tag"]
        return schema

    @classmethod
    def get_pull_properties(cls):
        # Inform Snapcraft of the properties associated with pulling. If these
        # change in the YAML Snapcraft will consider the build step dirty.
        return ["ruby-version"]

    def __init__(self, name, options, project):
        super().__init__(name, options, project)

        if project.info.base not in ("core", "core16", "core18"):
            raise errors.PluginBaseError(part_name=self.name, base=project.info.base)

        self._ruby_version = options.ruby_version
        self._ruby_part_dir = os.path.join(self.partdir, "ruby")
        feature_pattern = re.compile(r"^(\d+\.\d+)\..*$")
        feature_version = feature_pattern.sub(r"\1", self._ruby_version)
        self._ruby_download_url = "https://cache.ruby-lang.org/pub/ruby/{}/ruby-{}.tar.gz".format(
            feature_version, self._ruby_version
        )
        self._ruby_tar = Tar(self._ruby_download_url, self._ruby_part_dir)
        self.build_packages.extend(
            ["gcc", "g++", "make", "zlib1g-dev", "libssl-dev", "libreadline-dev"]
        )

    def pull(self):
        super().pull()
        os.makedirs(self._ruby_part_dir, exist_ok=True)

        logger.info("Fetching ruby {}...".format(self._ruby_version))
        self._ruby_tar.download()

        logger.info("Building/installing ruby...")
        self._ruby_install(builddir=self._ruby_part_dir)

    def build(self):
        super().build()

        source_tag = self.options.source_tag
        version = source_tag[1:] if source_tag.startswith("v") else source_tag

        path = os.path.join(self.sourcedir, '*.gemspec')
        path = glob.glob(path)[0]
        path = os.path.basename(path)

        gem_name = os.path.splitext(path)[0]

        self._run_gem('install', 'bundler')
        self._run_gem('build', '{}.gemspec'.format(gem_name))
        self._run_gem('install', "{}-{}.gem".format(gem_name, version))

    def env(self, root):
        env = super().env(root)

        for key, value in self._env_dict(root).items():
            env.append('{}="{}"'.format(key, value))

        return env

    def _env_dict(self, root):
        env = dict()
        rubydir = os.path.join(root, "lib", "ruby")

        # Patch versions of ruby continue to use the minor version's RUBYLIB,
        # GEM_HOME, and GEM_PATH. Fortunately there should just be one, so we
        # can detect it by globbing instead of trying to determine what the
        # minor version is programmatically
        versions = glob.glob(os.path.join(rubydir, "gems", "*"))

        # Before Ruby has been pulled/installed, no versions will be found.
        # If that's the case, we won't define any Ruby-specific variables yet
        if len(versions) == 1:
            ruby_version = os.path.basename(versions[0])

            rubylib = os.path.join(rubydir, ruby_version)

            # Ruby uses some pretty convoluted rules for determining its
            # arch-specific RUBYLIB. Rather than try and duplicate that logic
            # here, let's just look for a file that we know is in there:
            # rbconfig.rb. There should only be one.
            paths = glob.glob(os.path.join(rubylib, "*", "rbconfig.rb"))
            if len(paths) != 1:
                raise errors.SnapcraftEnvironmentError(
                    "Expected a single rbconfig.rb, but found {}".format(len(paths))
                )

            env["RUBYLIB"] = "{}:{}".format(rubylib, os.path.dirname(paths[0]))
            env["GEM_HOME"] = os.path.join(rubydir, "gems", ruby_version)
            env["GEM_PATH"] = os.path.join(rubydir, "gems", ruby_version)
        elif len(versions) > 1:
            raise errors.SnapcraftEnvironmentError(
                "Expected a single Ruby version, but found {}".format(len(versions))
            )

        return env

    def _run(self, command, **kwargs):
        """Regenerate the build environment, then run requested command.

        Without this function, the build environment would not be regenerated
        and thus the newly installed Ruby would not be discovered.
        """

        env = os.environ.copy()
        env.update(self._env_dict(self.installdir))
        self.run(command, env=env, **kwargs)

    def _ruby_install(self, builddir):
        self._ruby_tar.provision(builddir, clean_target=False, keep_tarball=True)
        self._run(["./configure", "--disable-install-rdoc", "--prefix=/"], cwd=builddir)
        self._run(["make", "-j{}".format(self.parallel_build_count)], cwd=builddir)
        self._run(
            ["make", "install", "DESTDIR={}".format(self.installdir)], cwd=builddir
        )
        # Fix all shebangs to use the in-snap ruby
        file_utils.replace_in_file(
            self.installdir,
            re.compile(r""),
            re.compile(r"^#!.*ruby"),
            r"#!/usr/bin/env ruby",
        )

    def _run_gem(self, *args):
        logger.info("Running gems {}...".format(args))
        gem_cmd = [
            os.path.join(self.installdir, "bin", "ruby"),
            os.path.join(self.installdir, "bin", "gem")
        ]

        self._run(gem_cmd + list(args))
