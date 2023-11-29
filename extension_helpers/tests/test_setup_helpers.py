import importlib
import os
import subprocess
import sys
import uuid
from textwrap import dedent

import pytest

from .._setup_helpers import get_compiler, get_extensions
from . import cleanup_import, run_setup

extension_helpers_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)  # noqa


def teardown_module(module):
    # Remove file generated by test_generate_openmp_enabled_py but
    # somehow needed in test_cython_autoextensions
    tmpfile = "openmp_enabled.py"
    if os.path.exists(tmpfile):
        os.remove(tmpfile)


POSSIBLE_COMPILERS = ["unix", "msvc", "bcpp", "cygwin", "mingw32"]


def test_get_compiler():
    assert get_compiler() in POSSIBLE_COMPILERS


def _extension_test_package(tmpdir, request, extension_type="c", include_numpy=False):
    """Creates a simple test package with an extension module."""

    test_pkg = tmpdir.mkdir("test_pkg")
    test_pkg.mkdir("helpers_test_package").ensure("__init__.py")

    # TODO: It might be later worth making this particular test package into a
    # reusable fixture for other build_ext tests

    if extension_type in ("c", "both"):
        # A minimal C extension for testing
        test_pkg.join("helpers_test_package", "unit01.c").write(
            dedent(
                """\
            #include <Python.h>

            static struct PyModuleDef moduledef = {
                PyModuleDef_HEAD_INIT,
                "unit01",
                NULL,
                -1,
                NULL
            };
            PyMODINIT_FUNC
            PyInit_unit01(void) {
                return PyModule_Create(&moduledef);
            }
        """
            )
        )

    if extension_type in ("pyx", "both"):
        # A minimal Cython extension for testing
        test_pkg.join("helpers_test_package", "unit02.pyx").write(
            dedent(
                """\
            print("Hello cruel angel.")
        """
            )
        )

    if extension_type == "c":
        extensions = ["unit01.c"]
    elif extension_type == "pyx":
        extensions = ["unit02.pyx"]
    elif extension_type == "both":
        extensions = ["unit01.c", "unit02.pyx"]

    include_dirs = ["numpy"] if include_numpy else []

    extensions_list = [
        f"Extension('helpers_test_package.{os.path.splitext(extension)[0]}', "
        f"[join('helpers_test_package', '{extension}')], "
        f"include_dirs={include_dirs})"
        for extension in extensions
    ]

    test_pkg.join("helpers_test_package", "setup_package.py").write(
        dedent(
            """\
        from setuptools import Extension
        from os.path import join
        def get_extensions():
            return [{}]
    """.format(
                ", ".join(extensions_list)
            )
        )
    )

    test_pkg.join("setup.py").write(
        dedent(
            f"""\
        import sys
        from os.path import join
        from setuptools import setup, find_packages
        sys.path.insert(0, r'{extension_helpers_PATH}')
        from extension_helpers import get_extensions

        setup(
            name='helpers_test_package',
            version='0.1',
            packages=find_packages(),
            ext_modules=get_extensions()
        )
    """
        )
    )

    if "" in sys.path:
        sys.path.remove("")

    sys.path.insert(0, "")

    def finalize():
        cleanup_import("helpers_test_package")

    request.addfinalizer(finalize)

    return test_pkg


@pytest.fixture
def extension_test_package(tmpdir, request):
    return _extension_test_package(tmpdir, request, extension_type="both")


@pytest.fixture
def c_extension_test_package(tmpdir, request):
    # Check whether numpy is installed in the test environment
    has_numpy = bool(importlib.util.find_spec("numpy"))
    return _extension_test_package(tmpdir, request, extension_type="c", include_numpy=has_numpy)


@pytest.fixture
def pyx_extension_test_package(tmpdir, request):
    return _extension_test_package(tmpdir, request, extension_type="pyx")


def test_cython_autoextensions(tmpdir):
    """
    Regression test for https://github.com/astropy/astropy-helpers/pull/19

    Ensures that Cython extensions in sub-packages are discovered and built
    only once.
    """

    # Make a simple test package
    test_pkg = tmpdir.mkdir("test_pkg")
    test_pkg.mkdir("yoda").mkdir("luke")
    test_pkg.ensure("yoda", "__init__.py")
    test_pkg.ensure("yoda", "luke", "__init__.py")
    test_pkg.join("yoda", "luke", "dagobah.pyx").write("""def testfunc(): pass""")

    # Required, currently, for get_extensions to work
    ext_modules = get_extensions(str(test_pkg))

    assert len(ext_modules) == 2
    assert ext_modules[0].name == "yoda.luke.dagobah"


def test_compiler_module(capsys, c_extension_test_package):
    """
    Test ensuring that the compiler module is built and installed for packages
    that have extension modules.
    """

    test_pkg = c_extension_test_package
    install_temp = test_pkg.mkdir("install_temp")

    with test_pkg.as_cwd():
        # This is one of the simplest ways to install just a package into a
        # test directory
        run_setup(
            "setup.py",
            [
                "install",
                "--single-version-externally-managed",
                f"--install-lib={install_temp}",
                "--record={}".format(install_temp.join("record.txt")),
            ],
        )

    with install_temp.as_cwd():
        import helpers_test_package

        # Make sure we imported the helpers_test_package package from the correct place
        dirname = os.path.abspath(os.path.dirname(helpers_test_package.__file__))
        assert dirname == str(install_temp.join("helpers_test_package"))

        import helpers_test_package.compiler_version

        assert helpers_test_package.compiler_version != "unknown"


@pytest.mark.parametrize("use_extension_helpers", [None, False, True])
@pytest.mark.parametrize("pyproject_use_helpers", [None, False, True])
def test_no_setup_py(tmpdir, use_extension_helpers, pyproject_use_helpers):
    """
    Test that makes sure that extension-helpers can be enabled without a
    setup.py file.
    """

    package_name = "helpers_test_package_" + str(uuid.uuid4()).replace("-", "_")

    test_pkg = tmpdir.mkdir("test_pkg")
    test_pkg.mkdir(package_name).ensure("__init__.py")

    simple_c = test_pkg.join(package_name, "simple.c")

    simple_c.write(
        dedent(
            """\
        #include <Python.h>

        static struct PyModuleDef moduledef = {
            PyModuleDef_HEAD_INIT,
            "simple",
            NULL,
            -1,
            NULL
        };
        PyMODINIT_FUNC
        PyInit_simple(void) {
            return PyModule_Create(&moduledef);
        }
    """
        )
    )

    test_pkg.join(package_name, "setup_package.py").write(
        dedent(
            f"""\
        from setuptools import Extension
        from os.path import join
        def get_extensions():
            return [Extension('{package_name}.simple', [join('{package_name}', 'simple.c')])]
        """
        )
    )

    if use_extension_helpers is None:
        test_pkg.join("setup.cfg").write(
            dedent(
                f"""\
            [metadata]
            name = {package_name}
            version = 0.1

            [options]
            packages = find:
        """
            )
        )
    else:
        test_pkg.join("setup.cfg").write(
            dedent(
                f"""\
            [metadata]
            name = {package_name}
            version = 0.1

            [options]
            packages = find:

            [extension-helpers]
            use_extension_helpers = {str(use_extension_helpers).lower()}
        """
            )
        )

    if pyproject_use_helpers is None:
        test_pkg.join("pyproject.toml").write(
            dedent(
                """\
            [build-system]
            requires = ["setuptools>=43.0.0",
                        "wheel"]
            build-backend = 'setuptools.build_meta'
        """
            )
        )
    else:
        test_pkg.join("pyproject.toml").write(
            dedent(
                f"""\
            [build-system]
            requires = ["setuptools>=43.0.0",
                        "wheel"]
            build-backend = 'setuptools.build_meta'

            [tool.extension-helpers]
            use_extension_helpers = {str(pyproject_use_helpers).lower()}
        """
            )
        )

    install_temp = test_pkg.mkdir("install_temp")

    with test_pkg.as_cwd():
        # NOTE: we disable build isolation as we need to pick up the current
        # developer version of extension-helpers
        subprocess.call(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                ".",
                "--no-build-isolation",
                f"--target={install_temp}",
            ]
        )

    if "" in sys.path:
        sys.path.remove("")

    sys.path.insert(0, "")

    with install_temp.as_cwd():
        importlib.import_module(package_name)

        if use_extension_helpers or (use_extension_helpers is None and pyproject_use_helpers):
            compiler_version_mod = importlib.import_module(package_name + ".compiler_version")
            assert compiler_version_mod.compiler != "unknown"
        else:
            try:
                importlib.import_module(package_name + ".compiler_version")
            except ImportError:
                pass
            else:
                raise AssertionError(package_name + ".compiler_version should not exist")


@pytest.mark.parametrize("pyproject_use_helpers", [None, False, True])
def test_only_pyproject(tmpdir, pyproject_use_helpers):
    """
    Test that makes sure that extension-helpers can be enabled without a
    setup.py and without a setup.cfg file.
    """

    pytest.importorskip("setuptools", minversion="62.0")

    package_name = "helpers_test_package_" + str(uuid.uuid4()).replace("-", "_")

    test_pkg = tmpdir.mkdir("test_pkg")
    test_pkg.mkdir(package_name).ensure("__init__.py")

    simple_pyx = test_pkg.join(package_name, "simple.pyx")
    simple_pyx.write(
        dedent(
            """\
        def test():
            pass
    """
        )
    )

    if pyproject_use_helpers is None:
        extension_helpers_option = ""
    else:
        extension_helpers_option = dedent(
            f"""
        [tool.extension-helpers]
        use_extension_helpers = {str(pyproject_use_helpers).lower()}
        """
        )

    test_pkg.join("pyproject.toml").write(
        dedent(
            f"""\
            [project]
            name = "{package_name}"
            version = "0.1"

            [tool.setuptools.packages]
            find = {{namespaces = false}}

            [build-system]
            requires = ["setuptools>=43.0.0",
                        "wheel",
                        "cython"]
            build-backend = 'setuptools.build_meta'

            """
        )
        + extension_helpers_option
    )

    install_temp = test_pkg.mkdir("install_temp")

    with test_pkg.as_cwd():
        # NOTE: we disable build isolation as we need to pick up the current
        # developer version of extension-helpers
        subprocess.call(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                ".",
                "--no-build-isolation",
                f"--target={install_temp}",
            ]
        )

    if "" in sys.path:
        sys.path.remove("")

    sys.path.insert(0, "")

    with install_temp.as_cwd():
        importlib.import_module(package_name)

        if pyproject_use_helpers:
            compiler_version_mod = importlib.import_module(package_name + ".compiler_version")
            assert compiler_version_mod.compiler != "unknown"
        else:
            try:
                importlib.import_module(package_name + ".compiler_version")
            except ImportError:
                pass
            else:
                raise AssertionError(package_name + ".compiler_version should not exist")
