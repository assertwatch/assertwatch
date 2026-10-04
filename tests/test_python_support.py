import pytest

from assertwatch.python_support import excludes_python2, python2_dropped


@pytest.mark.parametrize(
    ("spec", "excludes"),
    [
        (">=3.8", True),
        (">=3.8.1", True),
        ("~=3.9", True),
        ("^3.10", True),
        (">=3, <4", True),
        ("==3.*", True),
        ("3.11", True),
        (">=2.7, !=3.0.*, !=3.1.*", False),
        (">2.7", False),
        ("<4", False),
        ("!=2.7", False),
        ("*", False),
        ("", False),
    ],
)
def test_excludes_python2(spec, excludes):
    assert excludes_python2(spec) is excludes


def dropped(**files):
    return python2_dropped(lambda path: files.get(path.replace(".", "_")))


def test_pyproject_requires_python():
    assert dropped(pyproject_toml='[project]\nrequires-python = ">=3.9"\n')
    assert not dropped(pyproject_toml='[project]\nrequires-python = ">=2.7"\n')


def test_poetry_python_dependency():
    assert dropped(pyproject_toml='[tool.poetry.dependencies]\npython = "^3.10"\n')


def test_setup_py_and_setup_cfg():
    assert dropped(setup_py='setup(name="x", python_requires=">=3.7")\n')
    assert not dropped(setup_py='setup(python_requires=">=2.7, !=3.0.*")\n')
    assert dropped(setup_cfg="[options]\npython_requires = >=3.8\n")


def test_classifiers():
    py3_only = '"Programming Language :: Python :: 3 :: Only"'
    assert dropped(setup_py=f"setup(classifiers=[{py3_only}])\n")
    cfg = "[metadata]\nclassifiers =\n    Programming Language :: Python :: 3 :: Only\n"
    assert dropped(setup_cfg=cfg)


def test_any_sign_of_python2_support_wins():
    both = (
        '[project]\nrequires-python = ">=3.8"\n'
        'classifiers = ["Programming Language :: Python :: 2.7"]\n'
    )
    assert not dropped(pyproject_toml=both)
    assert not dropped(
        pyproject_toml='[project]\nrequires-python = ">=3.8"\n',
        setup_py='setup(python_requires=">=2.7")\n',
    )


@pytest.mark.parametrize(
    "files",
    [
        {},
        {"pyproject_toml": "[project]\nname = 'x'\n"},
        {"pyproject_toml": "not toml ["},
        {"pyproject_toml": "tool = 1\nproject = 2\n"},
        {"pyproject_toml": "[tool]\npoetry = 3\n"},
        {"setup_cfg": "not an ini file"},
        {"setup_py": "setup()"},
    ],
)
def test_unknown_support_is_not_dropped(files):
    assert not dropped(**files)
