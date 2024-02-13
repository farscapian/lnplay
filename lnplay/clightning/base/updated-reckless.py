#!/usr/bin/env python3

# updates from alex
# https://raw.githubusercontent.com/endothermicdev/lightning/reckless-github-api-limits/tools/reckless


import sys
import argparse
import copy
import datetime
from enum import Enum
import json
import logging
import os
from pathlib import Path, PosixPath
import shutil
from subprocess import Popen, PIPE, TimeoutExpired, run
import tempfile
import time
import types
from typing import Union
from urllib.parse import urlparse
from urllib.request import urlopen
import venv


logging.basicConfig(
    level=logging.DEBUG,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    handlers=[logging.StreamHandler(stream=sys.stdout)],
)


repos = ['https://github.com/lightningd/plugins']
GH_API_CALLS = 0


def py_entry_guesses(name) -> list:
    return [name, f'{name}.py', '__init__.py']


def unsupported_entry(name) -> list:
    return [f'{name}.go', f'{name}.sh']


def entry_guesses(name: str) -> list:
    guesses = []
    for inst in INSTALLERS:
        for entry in inst.entries:
            guesses.append(entry.format(name=name))
    return guesses


class Installer:
    '''
    The identification of a plugin language, compiler or interpreter
    availability, and the install procedures.
    '''
    def __init__(self, name: str, mimetype: str,
                 exe: Union[str, None] = None,
                 compiler: Union[str, None] = None,
                 manager: Union[str, None] = None,
                 entry: Union[str, None] = None):
        self.name = name
        self.mimetype = mimetype
        self.entries = []
        if entry:
            self.entries.append(entry)
        self.exe = exe            # interpreter (if required)
        self.compiler = compiler  # compiler bin (if required)
        self.manager = manager    # dependency manager (if required)
        self.dependency_file = None
        self.dependency_call = None

    def __repr__(self):
        return (f'<Installer {self.name}: mimetype: {self.mimetype}, '
                f'exe: {self.exe}, manager: {self.manager}>')

    def executable(self) -> bool:
        '''Validate the necessary bins are available to execute the plugin.'''
        if self.exe:
            if shutil.which(self.exe):
                # This should arguably not be checked here.
                if self.manager:
                    if shutil.which(self.manager):
                        return True
                    return False
                return True
            return False
        return True

    def installable(self) -> bool:
        '''Validate the necessary compiler and package manager executables are
        available to install.  If these are defined, they are considered
        mandatory even though the user may have the requisite packages already
        installed.'''
        if self.compiler and not shutil.which(self.compiler):
            return False
        if self.manager and not shutil.which(self.manager):
            return False
        return True

    def add_entrypoint(self, entry: str):
        assert isinstance(entry, str)
        self.entries.append(entry)

    def get_entrypoints(self, name: str):
        guesses = []
        for entry in self.entries:
            guesses.append(entry.format(name=name))
        return guesses

    def add_dependency_file(self, dep: str):
        assert isinstance(dep, str)
        self.dependency_file = dep

    def add_dependency_call(self, call: list):
        if self.dependency_call is None:
            self.dependency_call = []
        self.dependency_call.append(call)

    def copy(self):
        return copy.deepcopy(self)


class InstInfo:
    def __init__(self, name: str, location: str, git_url: str):
        self.name = name
        self.source_loc = str(location)     # Used for 'git clone'
        self.git_url = git_url              # API access for github repos
        self.srctype = Source.get_type(location)
        self.entry = None                   # relative to source_loc or subdir
        self.deps = None
        self.subdir = None
        self.commit = None

    def __repr__(self):
        return (f'InstInfo({self.name}, {self.source_loc}, {self.git_url}, '
                f'{self.entry}, {self.deps}, {self.subdir})')

    def get_inst_details(self) -> bool:
        """Search the source_loc for plugin install details.
        This may be necessary if a contents api is unavailable.
        Extracts entrypoint and dependencies if searchable, otherwise
        matches a directory to the plugin name and stops."""
        if self.srctype == Source.DIRECTORY:
            assert Path(self.source_loc).exists()
            assert os.path.isdir(self.source_loc)
        target = SourceDir(self.source_loc, srctype=self.srctype)
        # Set recursion for how many directories deep we should search
        depth = 0
        if self.srctype in [Source.DIRECTORY, Source.LOCAL_REPO]:
            depth = 5
        elif self.srctype == Source.GITHUB_REPO:
            depth = 1

        def search_dir(self, sub: SourceDir, subdir: bool,
                       recursion: int) -> Union[SourceDir, None]:
            assert isinstance(recursion, int)
            # carveout for archived plugins in lightningd/plugins
            if recursion == 0 and 'archive' in sub.name.lower():
                pass
            # If unable to search deeper, resort to matching directory name
            elif recursion < 1:
                if sub.name.lower() == self.name.lower():
                    # Partial success (can't check for entrypoint)
                    self.name = sub.name
                    return sub
                return None
            sub.populate()

            if sub.name.lower() == self.name.lower():
                # Directory matches the name we're trying to install, so check
                # for entrypoint and dependencies.
                for inst in INSTALLERS:
                    for g in inst.get_entrypoints(self.name):
                        found_entry = sub.find(g, ftype=SourceFile)
                        if found_entry:
                            break
                    # FIXME: handle a list of dependencies
                    found_dep = sub.find(inst.dependency_file,
                                         ftype=SourceFile)
                    if found_entry:
                        # Success!
                        if found_dep:
                            self.name = sub.name
                            self.entry = found_entry.name
                            self.deps = found_dep.name
                            return sub
                        logging.debug(f"missing dependency for {self}")
                        found_entry = None
            for file in sub.contents:
                if isinstance(file, SourceDir):
                    success = search_dir(self, file, True, recursion - 1)
                    if success:
                        return success
            return None

        result = search_dir(self, target, False, depth)
        if result:
            if result != target:
                if result.relative:
                    self.subdir = result.relative
            return True
        return False


def create_dir(directory: PosixPath) -> bool:
    try:
        Path(directory).mkdir(parents=False, exist_ok=True)
        return True
    # Okay if directory already exists
    except FileExistsError:
        return True
    # Parent directory missing
    except FileNotFoundError:
        return False


def remove_dir(directory: str) -> bool:
    try:
        shutil.rmtree(directory)
        return True
    except NotADirectoryError:
        print(f"Tried to remove directory {directory} that does not exist.")
    except PermissionError:
        print(f"Permission denied removing dir: {directory}")
    return False


class Source(Enum):
    DIRECTORY = 1
    LOCAL_REPO = 2
    GITHUB_REPO = 3
    OTHER_URL = 4
    UNKNOWN = 5

    @classmethod
    def get_type(cls, source: str):
        if Path(os.path.realpath(source)).exists():
            if os.path.isdir(os.path.realpath(source)):
                # returns 0 if git repository
                proc = run(['git', '-C', source, 'rev-parse'],
                           cwd=os.path.realpath(source), stdout=PIPE,
                           stderr=PIPE, text=True, timeout=5)
                if proc.returncode == 0:
                    return cls(2)
                return cls(1)
        if 'github.com' in source.lower():
            return cls(3)
        if 'http://' in source.lower() or 'https://' in source.lower():
            return cls(4)
        return cls(5)


class SourceDir():
    """Structure to search source contents."""
    def __init__(self, location: str, srctype: Source = None, name: str = None,
                 relative: str = None):
        self.location = str(location)
        if name:
            self.name = name
        else:
            self.name = Path(location).name
        self.contents = []
        self.srctype = srctype
        self.prepopulated = False
        self.relative = relative  # location relative to source

    def populate(self):
        """populates contents of the directory at least one level"""
        if self.prepopulated:
            return
        if not self.srctype:
            self.srctype = Source.get_type(self.location)
        # logging.debug(f"populating {self.srctype} {self.location}")
        if self.srctype == Source.DIRECTORY:
            self.contents = populate_local_dir(self.location)
        elif self.srctype == Source.LOCAL_REPO:
            self.contents = populate_local_repo(self.location)
        elif self.srctype == Source.GITHUB_REPO:
            self.contents = populate_github_repo(self.location)
        else:
            raise Exception("populate method undefined for {self.srctype}")
        # Ensure the relative path of the contents is inherited.
        for c in self.contents:
            if self.relative is None:
                c.relative = c.name
            else:
                c.relative = str(Path(self.relative) / c.name)

    def find(self, name: str, ftype: type = None) -> str:
        """Match a SourceFile or SourceDir to the provided name
        (case insentive) and return its filename."""
        assert isinstance(name, str)
        if len(self.contents) == 0:
            return None
        for c in self.contents:
            if ftype and not isinstance(c, ftype):
                continue
            if c.name.lower() == name.lower():
                return c
        return None

    def __repr__(self):
        return f"<SourceDir: {self.name} ({self.location})>"

    def __eq__(self, compared):
        if isinstance(compared, str):
            return self.name == compared
        if isinstance(compared, SourceDir):
            return (self.name == compared.name and
                    self.location == compared.location)
        return False


class SourceFile():
    def __init__(self, location: str):
        self.location = str(location)
        self.name = Path(location).name

    def __repr__(self):
        return f"<SourceFile: {self.name} ({self.location})>"

    def __eq__(self, compared):
        if isinstance(compared, str):
            return self.name == compared
        if isinstance(compared, SourceFile):
            return (self.name == compared.name and
                    self.location == compared.location)
        return False


def populate_local_dir(path: str) -> list:
    assert Path(os.path.realpath(path)).exists()
    contents = []
    for c in os.listdir(path):
        fullpath = Path(path) / c
        if os.path.isdir(fullpath):
            # Inheriting type saves a call to test if it's a git repo
            contents.append(SourceDir(fullpath, srctype=Source.DIRECTORY))
        else:
            contents.append(SourceFile(fullpath))
    return contents


def populate_local_repo(path: str) -> list:
    assert Path(os.path.realpath(path)).exists()
    basedir = SourceDir('base')

    def populate_source_path(parent, mypath):
        """`git ls-tree` lists all files with their full path.
        This populates all intermediate directories and the file."""
        parentdir = parent
        if mypath == '.':
            logging.debug(' asked to populate root dir')
            return
        # reverse the parents
        pdirs = mypath
        revpath = []
        child = parentdir
        while pdirs.parent.name != '':
            revpath.append(pdirs.parent.name)
            pdirs = pdirs.parent
        for p in reversed(revpath):
            child = parentdir.find(p)
            if child:
                parentdir = child
            else:
                child = SourceDir(p, srctype=Source.LOCAL_REPO)
                child.prepopulated = True
                parentdir.contents.append(child)
                parentdir = child
        newfile = SourceFile(mypath.name)
        child.contents.append(newfile)

    # FIXME: Pass in tag or commit hash
    ver = 'HEAD'
    git_call = ['git', '-C', path, 'ls-tree', '--full-tree', '-r',
                '--name-only', ver]
    proc = run(git_call, stdout=PIPE, stderr=PIPE, text=True, timeout=5)
    if proc.returncode != 0:
        logging.debug(f'ls-tree of repo {path} failed')
        return None
    for filepath in proc.stdout.splitlines():
        populate_source_path(basedir, Path(filepath))
    return basedir.contents


def source_element_from_repo_api(member: dict):
    # FIXME: remove this assert
    assert isinstance(member, dict)
    # api accessed via /contents
    if 'type' in member and 'name' in member and 'git_url' in member:
        if member['type'] == 'dir':
            return SourceDir(member['git_url'], srctype=Source.GITHUB_REPO,
                             name=member['name'])
        elif member['type'] == 'file':
            # Likely a submodule
            if member['size'] == 0:
                return SourceDir(None, srctype=Source.GITHUB_REPO,
                                 name=member['name'])
            return SourceFile(member['name'])
        # FIXME: Nope, this is by the other API
        elif member['type'] == 'commit':
            # No path is given by the api here
            return SourceDir(None, srctype=Source.GITHUB_REPO,
                             name=member['name'])
    # git_url with /tree presents results a little differently
    elif 'type' in member and 'path' in member and 'url' in member:
        if member['type'] not in ['tree', 'blob']:
            logging.debug(f'  skipping {member["path"]} type={member["type"]}')
        if member['type'] == 'tree':
            return SourceDir(member['url'], srctype=Source.GITHUB_REPO,
                             name=member['path'])
        elif member['type'] == 'blob':
            # This can be a submodule
            if member['size'] == 0:
                return SourceDir(member['git_url'], srctype=Source.GITHUB_REPO,
                                 name=member['name'])
            return SourceFile(member['path'])
    return None


def populate_github_repo(url: str) -> list:
    # FIXME: This probably contains leftover cruft.
    repo = url.split('/')
    while '' in repo:
        repo.remove('')
    repo_name = None
    parsed_url = urlparse(url)
    if 'github.com' not in parsed_url.netloc:
        return None
    if len(parsed_url.path.split('/')) < 2:
        return None
    start = 1
    # Maybe we were passed an api.github.com/repo/<user> url
    if 'api' in parsed_url.netloc:
        start += 1
    repo_user = parsed_url.path.split('/')[start]
    repo_name = parsed_url.path.split('/')[start + 1]

    # Get details from the github API.
    if API_GITHUB_COM in url:
        api_url = url
    else:
        api_url = f'{API_GITHUB_COM}/repos/{repo_user}/{repo_name}/contents/'

    git_url = api_url
    if "api.github.com" in git_url:
        # This lets us redirect to handle blackbox testing
        logging.debug(f'fetching from gh API: {git_url}')
        git_url = (API_GITHUB_COM + git_url.split("api.github.com")[-1])
    # Ratelimiting occurs for non-authenticated GH API calls at 60 in 1 hour.
    global GH_API_CALLS
    GH_API_CALLS += 1
    if GH_API_CALLS > 5:
        logging.warning('excessive github API calls. exiting.')
        sys.exit(1)
    r = urlopen(git_url, timeout=5)
    if r.status != 200:
        return False
    if 'git/tree' in git_url:
        tree = json.loads(r.read().decode())['tree']
    else:
        tree = json.loads(r.read().decode())
    contents = []
    for sub in tree:
        if source_element_from_repo_api(sub):
            contents.append(source_element_from_repo_api(sub))
    return contents


class Config():
    """A generic class for procuring, reading and editing config files"""
    def obtain_config(self,
                      config_path: str,
                      default_text: str,
                      warn: bool = False) -> str:
        """Return a config file from the desired location. Create one with
        default_text if it cannot be found."""
        if isinstance(config_path, type(None)):
            raise Exception("Generic config must be passed a config_path.")
        assert isinstance(config_path, str)
        # FIXME: warn if reckless dir exists, but conf not found
        if Path(config_path).exists():
            with open(config_path, 'r+') as f:
                config_content = f.readlines()
            return config_content
        print(f'config file not found: {config_path}')
        if warn:
            confirm = input('press [Y] to create one now.\n').upper() == 'Y'
        else:
            confirm = True
        if not confirm:
            sys.exit(1)
        parent_path = Path(config_path).parent
        # Create up to one parent in the directory tree.
        if create_dir(parent_path):
            with open(self.conf_fp, 'w') as f:
                f.write(default_text)
                # FIXME: Handle write failure
                return default_text
        else:
            logging.debug('could not create the parent directory ' +
                          parent_path)
            raise FileNotFoundError('invalid parent directory')

    def editConfigFile(self, addline: Union[str, None],
                       removeline: Union[str, None]):
        """Idempotent function to add and/or remove a single line each."""
        remove_these_lines = []
        with open(self.conf_fp, 'r') as reckless_conf:
            original = reckless_conf.readlines()
            empty_lines = []
            write_required = False
            for n, l in enumerate(original):
                if removeline and l.strip() == removeline.strip():
                    write_required = True
                    remove_these_lines.append(n)
                    continue
                if l.strip() == '':
                    empty_lines.append(n)
                    if n-1 in empty_lines:
                        # The white space is getting excessive.
                        remove_these_lines.append(n)
                        continue
            if not addline and not write_required:
                return
            # No write necessary if addline is already in config.
            if addline and not write_required:
                for line in original:
                    if line.strip() == addline.strip():
                        return
            with open(self.conf_fp, 'w') as conf_write:
                # no need to write if passed 'None'
                line_exists = not bool(addline)
                for n, l in enumerate(original):
                    if n not in remove_these_lines:
                        if n > 0:
                            conf_write.write(f'\n{l.strip()}')
                        else:
                            conf_write.write(l.strip())
                        if addline and addline.strip() == l.strip():
                            # addline is idempotent
                            line_exists = True
                if not line_exists:
                    conf_write.write(f'\n{addline}')

    def __init__(self, path: Union[str, None] = None,
                 default_text: Union[str, None] = None,
                 warn: bool = False):
        assert path is not None
        assert default_text is not None
        self.conf_fp = path
        self.content = self.obtain_config(self.conf_fp, default_text,
                                          warn=warn)


class RecklessConfig(Config):
    """Reckless config (by default, specific to the bitcoin network only.)
    This is inherited by the lightningd config and contains all reckless
    maintained plugins."""

    def enable_plugin(self, plugin_path: str):
        """Handle persistent plugin loading via config"""
        self.editConfigFile(f'plugin={plugin_path}',
                            f'disable-plugin={plugin_path}')

    def disable_plugin(self, plugin_path: str):
        """Handle persistent plugin disabling via config"""
        self.editConfigFile(f'disable-plugin={plugin_path}',
                            f'plugin={plugin_path}')

    def __init__(self, path: Union[str, None] = None,
                 default_text: Union[str, None] = None):
        if path is None:
            path = Path(LIGHTNING_DIR) / 'reckless' / 'bitcoin-reckless.conf'
        if default_text is None:
            default_text = (
                '# This configuration file is managed by reckless to activate '
                'and disable\n# reckless-installed plugins\n\n'
            )
        Config.__init__(self, path=str(path), default_text=default_text)
        self.reckless_dir = Path(path).parent


class LightningBitcoinConfig(Config):
    """lightningd config specific to the bitcoin network. This is inherited by
    the main lightningd config and in turn, inherits bitcoin-reckless.conf."""

    def __init__(self, path: Union[str, None] = None,
                 default_text: Union[str, None] = None,
                 warn: bool = True):
        if path is None:
            path = Path(LIGHTNING_DIR).joinpath('bitcoin', 'config')
        if default_text is None:
            default_text = "# This config was autopopulated by reckless\n\n"
        Config.__init__(self, path=str(path),
                        default_text=default_text, warn=warn)


class InferInstall():
    """Once a plugin is installed, we may need its directory and entrypoint"""
    def __init__(self, name: str):
        reck_contents = os.listdir(RECKLESS_CONFIG.reckless_dir)
        reck_contents_lower = {}
        for f in reck_contents:
            reck_contents_lower.update({f.lower(): f})

        def match_name(name) -> str:
            for tier in range(0, 10):
                # Look for each installers preferred entrypoint format first
                for inst in INSTALLERS:
                    fmt = inst.entries[tier]
                    if '{name}' in fmt:
                        pre = fmt.split('{name}')[0]
                        post = fmt.split('{name}')[-1]
                        if name.startswith(pre) and name.endswith(post):
                            return name.lstrip(pre).rstrip(post)
                    else:
                        if fmt == name:
                            return name
            return name

        name = match_name(name)
        if name.lower() in reck_contents_lower.keys():
            actual_name = reck_contents_lower[name.lower()]
            self.dir = Path(RECKLESS_CONFIG.reckless_dir).joinpath(actual_name)
        else:
            raise Exception(f"Could not find a reckless directory for {name}")
        plug_dir = Path(RECKLESS_CONFIG.reckless_dir).joinpath(actual_name)
        for guess in entry_guesses(actual_name):
            for content in plug_dir.iterdir():
                if content.name == guess:
                    self.entry = str(content)
                    self.name = actual_name
                    return
        raise Exception(f'plugin entrypoint not found in {self.dir}')


class InstallationFailure(Exception):
    "raised when pip fails to complete dependency installation"


def create_python3_venv(staged_plugin: InstInfo) -> InstInfo:
    "Create a virtual environment, install dependencies and test plugin."
    env_path = Path('.venv')
    env_path_full = Path(staged_plugin.source_loc) / env_path
    plugin_path = Path(staged_plugin.source_loc) / 'source'

    # subdir should always be None at this point
    if staged_plugin.subdir:
        logging.warning("cloned plugin contains subdirectory")
        plugin_path = plugin_path / staged_plugin.subdir

    if shutil.which('poetry') and staged_plugin.deps == 'pyproject.toml':
        logging.debug('configuring a python virtual environment (poetry) in '
                      f'{env_path_full}')
        # The virtual environment should be located with the plugin.
        # This installs it to .venv instead of in the global location.
        mod_poetry_env = os.environ
        mod_poetry_env['POETRY_VIRTUALENVS_IN_PROJECT'] = 'true'
        # This ensures poetry installs to a new venv even though one may
        # already be active (i.e., under CI)
        if 'VIRTUAL_ENV' in mod_poetry_env:
            del mod_poetry_env['VIRTUAL_ENV']
        # to avoid relocating and breaking the venv, symlink pyroject.toml
        # to the location of poetry's .venv dir
        (Path(staged_plugin.source_loc) / 'pyproject.toml') \
            .symlink_to(plugin_path / 'pyproject.toml')
        (Path(staged_plugin.source_loc) / 'poetry.lock') \
            .symlink_to(plugin_path / 'poetry.lock')

        # Avoid redirecting stdout in order to stream progress.
        # Timeout excluded as armv7 grpcio build/install can take 1hr.
        pip = run(['poetry', 'install', '--no-root'], check=False,
                  cwd=staged_plugin.source_loc, env=mod_poetry_env)

        (Path(staged_plugin.source_loc) / 'pyproject.toml').unlink()
        (Path(staged_plugin.source_loc) / 'poetry.lock').unlink()

    else:
        builder = venv.EnvBuilder(with_pip=True)
        builder.create(env_path_full)
        logging.debug('configuring a python virtual environment (pip) in '
                      f'{env_path_full}')
        logging.debug(f'virtual environment created in {env_path_full}.')
        if staged_plugin.deps == 'pyproject.toml':
            pip = run(['bin/pip', 'install', str(plugin_path)],
                      check=False, cwd=plugin_path)
        elif staged_plugin.deps == 'requirements.txt':
            pip = run([str(env_path_full / 'bin/pip'), 'install', '-r',
                       str(plugin_path / 'requirements.txt')],
                      check=False, cwd=plugin_path)
        else:
            logging.debug("no python dependency file")
    if pip and pip.returncode != 0:
        logging.debug("install to virtual environment failed")
        print('error encountered installing dependencies')
        raise InstallationFailure

    staged_plugin.venv = env_path
    print('dependencies installed successfully')
    return staged_plugin


def create_wrapper(plugin: InstInfo):
    '''The wrapper will activate the virtual environment for this plugin and
    then run the plugin from within the same process.'''
    assert hasattr(plugin, 'venv')
    venv_full_path = Path(plugin.source_loc) / plugin.venv
    with open(Path(plugin.source_loc) / plugin.entry, 'w') as wrapper:
        wrapper.write((f"#!{venv_full_path}/bin/python\n"
                       "import sys\n"
                       "import runpy\n\n"
                       f"if '{plugin.source_loc}/source' not in sys.path:\n"
                       f"    sys.path.append('{plugin.source_loc}/source')\n"
                       f"if '{plugin.source_loc}' in sys.path:\n"
                       f"    sys.path.remove('{plugin.source_loc}')\n"
                       f"runpy.run_module(\"{plugin.name}\", "
                       "{}, \"__main__\")"))
    wrapper_file = Path(plugin.source_loc) / plugin.entry
    wrapper_file.chmod(0o755)


def install_to_python_virtual_environment(cloned_plugin: InstInfo):
    '''Called during install in place of a subprocess.run list'''
    # Delete symlink so that a venv wrapper can take it's place
    (Path(cloned_plugin.source_loc) / cloned_plugin.entry).unlink()
    # The original entrypoint is imported as a python module - ensure
    # it has a .py extension. The wrapper can keep the original naming.
    entry = Path(cloned_plugin.source_loc) / 'source' / cloned_plugin.entry
    entry.rename(entry.with_suffix('.py'))
    create_python3_venv(cloned_plugin)
    if not hasattr(cloned_plugin, 'venv'):
        raise InstallationFailure
    logging.debug('virtual environment for cloned plugin: '
                  f'{cloned_plugin.venv}')
    create_wrapper(cloned_plugin)
    return cloned_plugin


python3venv = Installer('python3venv', 'text/x-python', exe='python3',
                        manager='pip', entry='{name}.py')
python3venv.add_entrypoint('{name}')
python3venv.add_entrypoint('__init__.py')
python3venv.add_dependency_file('requirements.txt')
python3venv.dependency_call = install_to_python_virtual_environment

poetryvenv = Installer('poetryvenv', 'text/x-python', exe='python3',
                       manager='poetry', entry='{name}.py')
poetryvenv.add_entrypoint('{name}')
poetryvenv.add_entrypoint('__init__.py')
poetryvenv.add_dependency_file('pyproject.toml')
poetryvenv.dependency_call = install_to_python_virtual_environment

pyprojectViaPip = Installer('pyprojectViaPip', 'text/x-python', exe='python3',
                            manager='pip', entry='{name}.py')
pyprojectViaPip.add_entrypoint('{name}')
pyprojectViaPip.add_entrypoint('__init__.py')
pyprojectViaPip.add_dependency_file('pyproject.toml')
pyprojectViaPip.dependency_call = install_to_python_virtual_environment


# Nodejs plugin installer
nodejs = Installer('nodejs', 'application/javascript', exe='node',
                   manager='npm', entry='{name}.js')
nodejs.add_entrypoint('{name}')
nodejs.add_dependency_call(['npm', 'install', '--omit=dev'])
nodejs.add_dependency_file('package.json')

INSTALLERS = [python3venv, poetryvenv, pyprojectViaPip, nodejs]


def help_alias(targets: list):
    if len(targets) == 0:
        parser.print_help(sys.stdout)
    else:
        print('try "reckless {} -h"'.format(' '.join(targets)))
        sys.exit(1)


def _source_search(name: str, source: str) -> Union[InstInfo, None]:
    """Identify source type, retrieve contents, and populate InstInfo
    if the relevant contents are found."""
    root_dir = SourceDir(source)
    source = InstInfo(name, root_dir.location, None)
    if source.get_inst_details():
        return source
    return None


def _git_clone(src: InstInfo, dest: Union[PosixPath, str]) -> bool:
    print(f'cloning {src.srctype} {src}')
    if src.srctype == Source.GITHUB_REPO:
        assert 'github.com' in src.source_loc
        source = f"{GITHUB_COM}" + src.source_loc.split("github.com")[-1]
    elif src.srctype in [Source.LOCAL_REPO, Source.OTHER_URL]:
        source = src.source_loc
    else:
        return False
    git = run(['git', 'clone', source, str(dest)], stdout=PIPE, stderr=PIPE,
              text=True, check=False, timeout=60)
    if git.returncode != 0:
        for line in git.stderr:
            logging.debug(line)
        if Path(dest).exists():
            remove_dir(str(dest))
        print('Error: Failed to clone repo')
        return False
    return True


def get_temp_reckless_dir() -> PosixPath:
    random_dir = 'reckless-{}'.format(str(hash(os.times()))[-9:])
    new_path = Path(tempfile.gettempdir()) / random_dir
    return new_path


def add_installation_metadata(installed: InstInfo,
                              original_request: InstInfo):
    """Document the install request and installation details for use when
    updating the plugin."""
    install_dir = Path(installed.source_loc)
    assert install_dir.is_dir()
    data = ('installation date\n'
            f'{datetime.date.today().isoformat()}\n'
            'installation time\n'
            f'{int(time.time())}\n'
            'original source\n'
            f'{original_request.source_loc}\n'
            'requested commit\n'
            f'{original_request.commit}\n'
            'installed commit\n'
            f'{installed.commit}\n')
    with open(install_dir / '.metadata', 'w') as metadata:
        metadata.write(data)


def _checkout_commit(orig_src: InstInfo,
                     cloned_src: InstInfo,
                     cloned_path: PosixPath):
    # Check out and verify commit/tag if source was a repository
    if orig_src.srctype in [Source.LOCAL_REPO, Source.GITHUB_REPO,
                            Source.OTHER_URL]:
        if orig_src.commit:
            logging.debug(f"Checking out {orig_src.commit}")
            checkout = Popen(['git', 'checkout', orig_src.commit],
                             cwd=str(cloned_path),
                             stdout=PIPE, stderr=PIPE)
            checkout.wait()
            if checkout.returncode != 0:
                print('failed to checkout referenced '
                      f'commit {orig_src.commit}')
                return None
        else:
            logging.debug("using latest commit of default branch")

        # Log the commit we actually used (for installation metadata)
        git = run(['git', 'rev-parse', 'HEAD'], cwd=str(cloned_path),
                  stdout=PIPE, stderr=PIPE, text=True, check=False, timeout=60)
        if git.returncode == 0:
            head_commit = git.stdout.splitlines()[0]
            logging.debug(f'checked out HEAD: {head_commit}')
            cloned_src.commit = head_commit
        else:
            logging.debug(f'unable to collect commit: {git.stderr}')
    else:
        if orig_src.commit:
            logging.warning("unable to checkout commit/tag on non-repository "
                            "source")
        return cloned_path

    if cloned_src.subdir is not None:
        return Path(cloned_src.source_loc) / cloned_src.subdir
    return cloned_path


def _install_plugin(src: InstInfo) -> Union[InstInfo, None]:
    """make sure the repo exists and clone it."""
    logging.debug(f'Install requested from {src}.')
    if RECKLESS_CONFIG is None:
        print('error: reckless install directory unavailable')
        sys.exit(2)

    # Use a unique directory for each cloned repo.
    tmp_path = get_temp_reckless_dir()
    if not create_dir(tmp_path):
        logging.debug(f'failed to create {tmp_path}')
        return None
    clone_path = tmp_path / 'clone'
    if not create_dir(tmp_path):
        logging.debug(f'failed to create {clone_path}')
        return None
    # we rename the original repo here.
    plugin_path = clone_path / src.name
    inst_path = Path(RECKLESS_CONFIG.reckless_dir) / src.name
    if Path(clone_path).exists():
        logging.debug(f'{clone_path} already exists - deleting')
        shutil.rmtree(clone_path)
    if src.srctype == Source.DIRECTORY:
        logging.debug(("copying local directory contents from"
                       f" {src.source_loc}"))
        create_dir(clone_path)
        shutil.copytree(src.source_loc, plugin_path)
    elif src.srctype in [Source.LOCAL_REPO, Source.GITHUB_REPO,
                         Source.OTHER_URL]:
        # clone git repository to /tmp/reckless-...
        if not _git_clone(src, plugin_path):
            return None
    # FIXME: Validate path was cloned successfully.
    # Depending on how we accessed the original source, there may be install
    # details missing. Searching the cloned repo makes sure we have it.
    cloned_src = _source_search(src.name, str(clone_path))
    logging.debug(f'cloned_src: {cloned_src}')
    if not cloned_src:
        logging.debug('failed to find plugin after cloning repo.')
        return None

    # If a specific commit or tag was requested, check it out now.
    plugin_path = _checkout_commit(src, cloned_src, plugin_path)
    if not plugin_path:
        return None

    # Find a suitable installer
    INSTALLER = None
    for inst_method in INSTALLERS:
        if not (inst_method.installable() and inst_method.executable()):
            continue
        if inst_method.dependency_file is not None:
            if inst_method.dependency_file not in os.listdir(plugin_path):
                continue
        logging.debug(f"using installer {inst_method.name}")
        INSTALLER = inst_method
        break
    if not INSTALLER:
        logging.debug('Could not find a suitable installer method.')
        return None
    if not cloned_src.entry:
        # The plugin entrypoint may not be discernable prior to cloning.
        # Need to search the newly cloned directory, not the original
        cloned_src.source_loc = plugin_path

    # Relocate plugin to a staging directory prior to testing
    staging_path = inst_path / 'source'
    shutil.copytree(str(plugin_path), staging_path)
    staged_src = cloned_src
    # Because the source files are copied to a 'source' directory, the
    # get_inst_details function no longer works. (dir must match plugin name)
    # Set these manually instead.
    staged_src.source_loc = str(staging_path.parent)
    staged_src.srctype = Source.DIRECTORY
    staged_src.subdir = None
    # Create symlink in staging tree to redirect to the plugins entrypoint
    Path(staging_path.parent / cloned_src.entry).\
        symlink_to(staging_path / cloned_src.entry)

    # try it out
    if INSTALLER.dependency_call:
        if isinstance(INSTALLER.dependency_call, types.FunctionType):
            try:
                staged_src = INSTALLER.dependency_call(staged_src)
            except InstallationFailure:
                return None
        else:
            for call in INSTALLER.dependency_call:
                logging.debug(f"Install: invoking '{' '.join(call)}'")
                if logging.root.level < logging.WARNING:
                    pip = Popen(call, cwd=staging_path, text=True)
                else:
                    pip = Popen(call, cwd=staging_path, stdout=PIPE,
                                stderr=PIPE, text=True)
                pip.wait()
                # FIXME: handle output of multiple calls

            if pip.returncode == 0:
                print('dependencies installed successfully')
            else:
                print('error encountered installing dependencies')
                if pip.stdout:
                    logging.debug(pip.stdout.read())
                remove_dir(clone_path)
                remove_dir(inst_path)
                return None
    test_log = []
    try:
        test = run([Path(staged_src.source_loc).joinpath(staged_src.entry)],
                   cwd=str(staging_path), stdout=PIPE, stderr=PIPE,
                   text=True, timeout=10)
        for line in test.stderr.splitlines():
            test_log.append(line)
        returncode = test.returncode
    except TimeoutExpired:
        # If the plugin is still running, it's assumed to be okay.
        returncode = 0
    if returncode != 0:
        logging.debug("plugin testing error:")
        for line in test_log:
            logging.debug(f'  {line}')
        print('plugin testing failed')
        remove_dir(clone_path)
        remove_dir(inst_path)
        return None

    add_installation_metadata(staged_src, src)
    print(f'plugin installed: {inst_path}')
    remove_dir(clone_path)
    return staged_src


def install(plugin_name: str):
    """downloads plugin from source repos, installs and activates plugin"""
    assert isinstance(plugin_name, str)
    # Specify a tag or commit to checkout by adding @<tag> to plugin name
    if '@' in plugin_name:
        logging.debug("testing for a commit/tag in plugin name")
        name, commit = plugin_name.split('@', 1)
    else:
        name = plugin_name
        commit = None
    logging.debug(f"Searching for {name}")
    src = search(name)
    if src:
        src.commit = commit
        logging.debug(f'Retrieving {src.name} from {src.source_loc}')
        installed = _install_plugin(src)
        if not installed:
            print('installation aborted')
            sys.exit(1)

        # Match case of the containing directory
        for dirname in os.listdir(RECKLESS_CONFIG.reckless_dir):
            if dirname.lower() == installed.name.lower():
                inst_path = Path(RECKLESS_CONFIG.reckless_dir)
                inst_path = inst_path / dirname / installed.entry
                RECKLESS_CONFIG.enable_plugin(inst_path)
                enable(installed.name)
                return
        print(('dynamic activation failed: '
               f'{installed.name} not found in reckless directory'))
        sys.exit(1)


def uninstall(plugin_name: str):
    """disables plugin and deletes the plugin's reckless dir"""
    assert isinstance(plugin_name, str)
    logging.debug(f'Uninstalling plugin {plugin_name}')
    disable(plugin_name)
    inst = InferInstall(plugin_name)
    if not Path(inst.entry).exists():
        print(f'cannot find installed plugin at expected path {inst.entry}')
        sys.exit(1)
    logging.debug(f'looking for {str(Path(inst.entry).parent)}')
    if remove_dir(str(Path(inst.entry).parent)):
        print(f"{inst.name} uninstalled successfully.")


def search(plugin_name: str) -> Union[InstInfo, None]:
    """searches plugin index for plugin"""
    ordered_sources = RECKLESS_SOURCES

    for src in RECKLESS_SOURCES:
        # Search repos named after the plugin before collections
        if Source.get_type(src) == Source.GITHUB_REPO:
            if src.split('/')[-1].lower() == plugin_name.lower():
                ordered_sources.remove(src)
                ordered_sources.insert(0, src)
    # Check locally before reaching out to remote repositories
    for src in RECKLESS_SOURCES:
        if Source.get_type(src) in [Source.DIRECTORY, Source.LOCAL_REPO]:
            ordered_sources.remove(src)
            ordered_sources.insert(0, src)
    for source in ordered_sources:
        srctype = Source.get_type(source)
        if srctype == Source.UNKNOWN:
            logging.debug(f'cannot search {srctype} {source}')
            continue
        if srctype in [Source.DIRECTORY, Source.LOCAL_REPO,
                       Source.GITHUB_REPO, Source.OTHER_URL]:
            found = _source_search(plugin_name, source)
        if not found:
            continue
        print(f"found {found.name} in source: {found.source_loc}")
        logging.debug(f"entry: {found.entry}")
        if found.subdir:
            logging.debug(f'sub-directory: {found.subdir}')
        return found
    logging.debug("Search exhausted all sources")
    return None


class RPCError(Exception):
    """lightning-cli fails to connect to lightningd RPC"""
    def __init__(self, err):
        self.err = err

    def __str__(self):
        return 'RPCError({self.err})'


class CLIError(Exception):
    """lightningd error response"""
    def __init__(self, code, message):
        self.code = code
        self.message = message

    def __str__(self):
        return f'CLIError({self.code} {self.message})'


def lightning_cli(*cli_args, timeout: int = 15) -> dict:
    """Interfaces with Core-Lightning via CLI using any configured options."""
    cmd = LIGHTNING_CLI_CALL.copy()
    cmd.extend(cli_args)
    clncli = run(cmd, stdout=PIPE, stderr=PIPE, check=False, timeout=timeout)
    out = clncli.stdout.decode()
    if len(out) > 0 and out[0] == '{':
        # If all goes well, a json object is typically returned
        out = json.loads(out.replace('\n', ''))
    else:
        # help, -V, etc. may not return json, so stash it here.
        out = {'content': out}
    if clncli.returncode == 0:
        return out
    if clncli.returncode == 1:
        # RPC doesn't like our input
        # output contains 'code' and 'message'
        raise CLIError(out['code'], out['message'])
    # RPC may not be available - i.e., lightningd not running, using
    # alternate config.
    err = clncli.stderr.decode()
    raise RPCError(err)


def enable(plugin_name: str):
    """dynamically activates plugin and adds to config (persistent)"""
    assert isinstance(plugin_name, str)
    inst = InferInstall(plugin_name)
    path = inst.entry
    if not Path(path).exists():
        print(f'cannot find installed plugin at expected path {path}')
        sys.exit(1)
    logging.debug(f'activating {plugin_name}')
    try:
        lightning_cli('plugin', 'start', path)
    except CLIError as err:
        if 'already registered' in err.message:
            logging.debug(f'{inst.name} is already running')
        else:
            print(f'reckless: {inst.name} failed to start!')
            raise err
    except RPCError:
        logging.debug(('lightningd rpc unavailable. '
                       'Skipping dynamic activation.'))
    RECKLESS_CONFIG.enable_plugin(path)
    print(f'{inst.name} enabled')


def disable(plugin_name: str):
    """reckless disable <plugin>
    deactivates an installed plugin"""
    assert isinstance(plugin_name, str)
    inst = InferInstall(plugin_name)
    path = inst.entry
    if not Path(path).exists():
        sys.stderr.write(f'Could not find plugin at {path}\n')
        sys.exit(1)
    logging.debug(f'deactivating {plugin_name}')
    try:
        lightning_cli('plugin', 'stop', path)
    except CLIError as err:
        if err.code == -32602:
            logging.debug('plugin not currently running')
        else:
            print('lightning-cli plugin stop failed')
            raise err
    except RPCError:
        logging.debug(('lightningd rpc unavailable. '
                       'Skipping dynamic deactivation.'))
    RECKLESS_CONFIG.disable_plugin(path)
    print(f'{inst.name} disabled')


def load_config(reckless_dir: Union[str, None] = None,
                network: str = 'bitcoin') -> Config:
    """Initial directory discovery and config file creation."""
    net_conf = None
    # Does the lightning-cli already reference an explicit config?
    try:
        active_config = lightning_cli('listconfigs', timeout=10)['configs']
        if 'conf' in active_config:
            net_conf = LightningBitcoinConfig(path=active_config['conf']
                                              ['value_str'])
    except RPCError:
        pass
    if reckless_dir is None:
        reckless_dir = Path(LIGHTNING_DIR) / 'reckless'
    else:
        if not os.path.isabs(reckless_dir):
            reckless_dir = Path.cwd() / reckless_dir
    if LIGHTNING_CONFIG:
        network_path = LIGHTNING_CONFIG
    else:
        network_path = Path(LIGHTNING_DIR) / network / 'config'
    reck_conf_path = Path(reckless_dir) / f'{network}-reckless.conf'
    if net_conf:
        if str(network_path) != net_conf.conf_fp:
            print('error: reckless configuration does not match lightningd:\n'
                  f'reckless network config path: {network_path}\n'
                  f'lightningd active config: {net_conf.conf_fp}')
            sys.exit(1)
    else:
        # The network-specific config file (bitcoin by default)
        net_conf = LightningBitcoinConfig(path=network_path)
    # Reckless manages plugins here.
    try:
        reckless_conf = RecklessConfig(path=reck_conf_path)
    except FileNotFoundError:
        print('Error: reckless config file could not be written: ',
              str(reck_conf_path))
        sys.exit(1)
    if not net_conf:
        print('Error: could not load or create the network specific lightningd'
              ' config (default .lightning/bitcoin)')
        sys.exit(1)
    net_conf.editConfigFile(f'include {reckless_conf.conf_fp}', None)
    return reckless_conf


def get_sources_file() -> str:
    return str(Path(RECKLESS_DIR) / '.sources')


def sources_from_file() -> list:
    sources_file = get_sources_file()
    read_sources = []
    with open(sources_file, 'r') as f:
        for src in f.readlines():
            if len(src.strip()) > 0:
                read_sources.append(src.strip())
        return read_sources


def load_sources() -> list:
    """Look for the repo sources file."""
    sources_file = get_sources_file()
    # This would have been created if possible
    if not Path(sources_file).exists():
        logging.debug('Warning: Reckless requires write access')
        Config(path=str(sources_file),
               default_text='https://github.com/lightningd/plugins')
        return ['https://github.com/lightningd/plugins']
    return sources_from_file()


def add_source(src: str):
    """Additional git repositories, directories, etc. are passed here."""
    assert isinstance(src, str)
    # Is it a file?
    maybe_path = os.path.realpath(src)
    if Path(maybe_path).exists():
        if os.path.isdir(maybe_path):
            default_repo = 'https://github.com/lightningd/plugins'
            my_file = Config(path=str(get_sources_file()),
                             default_text=default_repo)
            my_file.editConfigFile(src, None)
    elif 'github.com' in src or 'http://' in src or 'https://' in src:
        my_file = Config(path=str(get_sources_file()),
                         default_text='https://github.com/lightningd/plugins')
        my_file.editConfigFile(src, None)
    else:
        print(f'failed to add source {src}')


def remove_source(src: str):
    """Remove a source from the sources file."""
    assert isinstance(src, str)
    if src in sources_from_file():
        my_file = Config(path=get_sources_file(),
                         default_text='https://github.com/lightningd/plugins')
        my_file.editConfigFile(None, src)
        print('plugin source removed')
    else:
        print(f'source not found: {src}')


def list_source():
    """Provide the user with all stored source repositories."""
    for src in sources_from_file():
        print(src)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    # This default depends on the .lightning directory
    parser.add_argument('-d', '--reckless-dir',
                        help='specify a data directory for reckless to use',
                        type=str, default=None)
    parser.add_argument('-l', '--lightning',
                        help='lightning data directory (default:~/.lightning)',
                        type=str,
                        default=Path.home().joinpath('.lightning'))
    parser.add_argument('-c', '--conf',
                        help=' config file used by lightningd',
                        type=str,
                        default=None)
    parser.add_argument('-r', '--regtest', action='store_true')
    parser.add_argument('--network',
                        help="specify a network to use (default: bitcoin)",
                        type=str)
    parser.add_argument('-v', '--verbose', action="store_const",
                        dest="loglevel", const=logging.DEBUG,
                        default=logging.WARNING)
    cmd1 = parser.add_subparsers(dest='cmd1', help='command',
                                 required=True)

    install_cmd = cmd1.add_parser('install', help='search for and install a '
                                  'plugin, then test and activate')
    install_cmd.add_argument('targets', type=str, nargs='*')
    install_cmd.set_defaults(func=install)

    uninstall_cmd = cmd1.add_parser('uninstall', help='deactivate a plugin '
                                    'and remove it from the directory')
    uninstall_cmd.add_argument('targets', type=str, nargs='*')
    uninstall_cmd.set_defaults(func=uninstall)

    search_cmd = cmd1.add_parser('search', help='search for a plugin from '
                                 'the available source repositories')
    search_cmd.add_argument('targets', type=str, nargs='*')
    search_cmd.set_defaults(func=search)

    enable_cmd = cmd1.add_parser('enable', help='dynamically enable a plugin '
                                 'and update config')
    enable_cmd.add_argument('targets', type=str, nargs='*')
    enable_cmd.set_defaults(func=enable)
    disable_cmd = cmd1.add_parser('disable', help='disable a plugin')
    disable_cmd.add_argument('targets', type=str, nargs='*')
    disable_cmd.set_defaults(func=disable)
    source_parser = cmd1.add_parser('source', help='manage plugin search '
                                    'sources')
    source_subs = source_parser.add_subparsers(dest='source_subs',
                                               required=True)
    list_parse = source_subs.add_parser('list', help='list available plugin '
                                        'sources (repositories)')
    list_parse.set_defaults(func=list_source)
    source_add = source_subs.add_parser('add', help='add a source repository')
    source_add.add_argument('targets', type=str, nargs='*')
    source_add.set_defaults(func=add_source)
    source_rem = source_subs.add_parser('remove', aliases=['rem', 'rm'],
                                        help='remove a plugin source '
                                        'repository')
    source_rem.add_argument('targets', type=str, nargs='*')
    source_rem.set_defaults(func=remove_source)

    help_cmd = cmd1.add_parser('help', help='for contextual help, use '
                               '"reckless <cmd> -h"')
    help_cmd.add_argument('targets', type=str, nargs='*')
    help_cmd.set_defaults(func=help_alias)

    args = parser.parse_args()

    NETWORK = 'regtest' if args.regtest else 'bitcoin'
    SUPPORTED_NETWORKS = ['bitcoin', 'regtest', 'liquid', 'liquid-regtest',
                          'litecoin', 'signet', 'testnet']
    if args.network:
        if args.network in SUPPORTED_NETWORKS:
            NETWORK = args.network
        else:
            print(f"Error: {args.network} network not supported")
    LIGHTNING_DIR = Path(args.lightning)
    # This env variable is set under CI testing
    LIGHTNING_CLI_CALL = [os.environ.get('LIGHTNING_CLI')]
    if LIGHTNING_CLI_CALL == [None]:
        LIGHTNING_CLI_CALL = ['lightning-cli']
    if NETWORK != 'bitcoin':
        LIGHTNING_CLI_CALL.append(f'--network={NETWORK}')
    if LIGHTNING_DIR != Path.home().joinpath('.lightning'):
        LIGHTNING_CLI_CALL.append(f'--lightning-dir={LIGHTNING_DIR}')
    if args.reckless_dir:
        RECKLESS_DIR = args.reckless_dir
    else:
        RECKLESS_DIR = Path(LIGHTNING_DIR) / 'reckless'
    LIGHTNING_CONFIG = args.conf
    RECKLESS_CONFIG = load_config(reckless_dir=RECKLESS_DIR,
                                  network=NETWORK)
    RECKLESS_SOURCES = load_sources()
    API_GITHUB_COM = 'https://api.github.com'
    GITHUB_COM = 'https://github.com'
    # Used for blackbox testing to avoid hitting github servers
    if 'REDIR_GITHUB_API' in os.environ:
        API_GITHUB_COM = os.environ['REDIR_GITHUB_API']
    if 'REDIR_GITHUB' in os.environ:
        GITHUB_COM = os.environ['REDIR_GITHUB']
    logging.root.setLevel(args.loglevel)

    if 'targets' in args:
        # FIXME: Catch missing argument
        if args.func.__name__ == 'help_alias':
            args.func(args.targets)
            sys.exit(0)
        for target in args.targets:
            args.func(target)
    else:
        args.func()
    if GH_API_CALLS > 0:
        logging.debug(f'GitHub API call total: {GH_API_CALLS}')