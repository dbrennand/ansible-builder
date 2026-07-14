import json
import logging
import os
import subprocess
import sys
import pytest

from ansible_builder._target_scripts.introspect import (PythonDependencyResolutionError,
                                                        extract_transitive_python_dependencies,
                                                        parse_args,
                                                        process,
                                                        process_collection,
                                                        filter_requirements,
                                                        resolve_python_dependencies,
                                                        run_introspect,
                                                        strip_comments,
                                                        write_python_dependency_outputs)


def test_multiple_collection_metadata(data_dir):
    files = process(data_dir=data_dir)
    files['python'] = filter_requirements(files['python'])
    files['system'] = filter_requirements(files['system'], is_python=False)

    assert files == {'python': [
        'pyvcloud>=14  # from collection test.metadata',
        'pytz  # from collection test.reqfile',
        'python-dateutil>=2.8.2  # from collection test.reqfile',
        'jinja2>=3.0  # from collection test.reqfile',
        'tacacs_plus  # from collection test.reqfile',
        'pyvcloud>=18.0.10  # from collection test.reqfile'
    ], 'system': [
        'subversion [platform:rpm]  # from collection test.bindep',
        'subversion [platform:dpkg]  # from collection test.bindep'
    ]}


def test_process_returns_excluded_python(data_dir, tmp_path):
    """
    Test that process() return value is properly formatted for excluded Python reqs.
    """
    pip_ignore_file = tmp_path / "exclude-requirements.txt"
    pip_ignore_file.write_text("req1\nreq2")

    retval = process(data_dir=data_dir, exclude_pip=str(pip_ignore_file))

    assert 'python' in retval
    assert 'exclude' in retval['python']
    assert retval['python']['exclude'] == ['req1', 'req2']


def test_process_returns_excluded_system(data_dir, tmp_path):
    """
    Test that process() return value is properly formatted for excluded system reqs.
    """
    bindep_ignore_file = tmp_path / "exclude-bindep.txt"
    bindep_ignore_file.write_text("req1\nreq2")

    retval = process(data_dir=data_dir, exclude_bindep=str(bindep_ignore_file))

    assert 'system' in retval
    assert 'exclude' in retval['system']
    assert retval['system']['exclude'] == ['req1', 'req2']


def test_process_returns_excluded_collections(data_dir, tmp_path):
    """
    Test that process() return value is properly formatted for excluded collections.
    """
    col_ignore_file = tmp_path / "ignored_collections"
    col_ignore_file.write_text("a.b\nc.d")

    retval = process(data_dir=data_dir, exclude_collections=str(col_ignore_file))

    assert 'excluded_collections' in retval
    assert retval['excluded_collections'] == ['a.b', 'c.d']


def test_single_collection_metadata(data_dir):

    col_path = os.path.join(data_dir, 'ansible_collections', 'test', 'metadata')
    py_reqs, sys_reqs = process_collection(col_path)

    assert py_reqs == ['pyvcloud>=14']
    assert not sys_reqs


def test_parse_args_empty(capsys):
    with pytest.raises(SystemExit):
        parse_args()
    dummy, err = capsys.readouterr()
    assert 'usage: introspect' in err


def test_parse_args_default_action():
    action = 'introspect'
    user_pip = '/tmp/user-pip.txt'
    user_bindep = '/tmp/user-bindep.txt'
    write_pip = '/tmp/write-pip.txt'
    write_bindep = '/tmp/write-bindep.txt'
    write_report = '/tmp/python-dependencies.json'
    write_transitive = '/tmp/transitive-requirements.txt'

    parser = parse_args(
        [
            action,
            f'--user-pip={user_pip}',
            f'--user-bindep={user_bindep}',
            f'--write-pip={write_pip}',
            f'--write-bindep={write_bindep}',
            f'--write-python-dependency-report={write_report}',
            f'--write-transitive-python={write_transitive}',
        ]
    )

    assert parser.action == action
    assert parser.user_pip == user_pip
    assert parser.user_bindep == user_bindep
    assert parser.write_pip == write_pip
    assert parser.write_bindep == write_bindep
    assert parser.write_python_dependency_report == write_report
    assert parser.write_transitive_python == write_transitive


def test_parse_args_dependency_outputs_default_to_none():
    parser = parse_args(['introspect'])

    assert parser.write_python_dependency_report is None
    assert parser.write_transitive_python is None


def test_yaml_extension(data_dir):
    """
    Test that introspection recognizes a collection meta directory EE with a .yaml file extension.

    NOTE: This test depends on the meta EE in the collection to reference a file other than "requirements.txt"
    because of the way CollectionDefinition.__init__() will fall through to a default if the meta EE is not
    found.
    """
    col_path = os.path.join(data_dir, 'alternate_collections')
    files = process(data_dir=col_path)
    assert files == {
        'python': {'test_collection.test_yaml_extension': ['python-six']},
        'system': {},
    }


def test_filter_requirements_pep508():
    reqs = {
        'a.b': [
            'foo[ext1,ext3] == 1',
            'bar; python_version < "2.7"',
            'A',
            "name",
        ],
        'c.d': [
            'FOO >= 1',
            'bar; python_version < "3.6"',
            "name<=1",
        ],
        'e.f': [
            'foo[ext2] @ git+http://github.com/foo/foo.git',
            "name>=3",
        ],
        'g.h': [
            "name>=3,<2",
        ],
        'i.j': [
            "name@http://foo.com",
        ],
        'k.l': [
            "name [fred,bar] @ http://foo.com ; python_version=='2.7'",
        ],
        'm.n': [
            "name[quux, strange];python_version<'2.7' and platform_version=='2'",
        ],
    }

    expected = [
        'foo[ext1,ext3] == 1  # from collection a.b',
        'bar; python_version < "2.7"  # from collection a.b',
        'A  # from collection a.b',
        'name  # from collection a.b',
        'FOO >= 1  # from collection c.d',
        'bar; python_version < "3.6"  # from collection c.d',
        'name<=1  # from collection c.d',
        'foo[ext2] @ git+http://github.com/foo/foo.git  # from collection e.f',
        'name>=3  # from collection e.f',
        'name>=3,<2  # from collection g.h',
        'name@http://foo.com  # from collection i.j',
        "name [fred,bar] @ http://foo.com ; python_version=='2.7'  # from collection k.l",
        "name[quux, strange];python_version<'2.7' and platform_version=='2'  # from collection m.n"
    ]

    assert filter_requirements(reqs) == expected


def test_comment_parsing():
    """
    Test that filter_requirements() does not remove embedded URL anchors due to comment parsing.
    """
    reqs = {
        'a.b': [
            '# comment 1',
            'git+https://git.repo/some_pkg.git#egg=SomePackage',
            'git+https://git.repo/some_pkg.git#egg=SomeOtherPackage  # inline comment',
            'git+https://git.repo/some_pkg.git#egg=AlsoSomePackage #inline comment that hates leading spaces',
            '    # crazy indented comment (waka waka!)',
            '####### something informative'
            '    ',
            '',
        ]
    }

    expected = [
        'git+https://git.repo/some_pkg.git#egg=SomePackage',
        'git+https://git.repo/some_pkg.git#egg=SomeOtherPackage',
        'git+https://git.repo/some_pkg.git#egg=AlsoSomePackage',
    ]

    assert filter_requirements(reqs) == expected


def test_strip_comments():
    """
    Test that strip_comments() properly removes comments from Python requirements input.
    """
    reqs = {
        'a.b': [
            '# comment 1',
            'git+https://git.repo/some_pkg.git#egg=SomePackage',
            'git+https://git.repo/some_pkg.git#egg=SomeOtherPackage  # inline comment',
            'git+https://git.repo/some_pkg.git#egg=AlsoSomePackage #inline comment that hates leading spaces',
            '    # crazy indented comment (waka waka!)',
            '####### something informative'
            '    ',
            '',
        ],
        'c.d': [
            '# comment 2',
            'git',
        ]
    }

    expected = {
        'a.b': [
            'git+https://git.repo/some_pkg.git#egg=SomePackage',
            'git+https://git.repo/some_pkg.git#egg=SomeOtherPackage',
            'git+https://git.repo/some_pkg.git#egg=AlsoSomePackage',
        ],
        'c.d': [
            'git',
        ]
    }

    assert strip_comments(reqs) == expected


def test_python_pass_thru():
    """
    Test that filter_requirements() will pass through non-pep508 data.
    """
    reqs = {
        # various VCS and URL options
        'a.b': [
            'git+https://git.repo/some_pkg.git#egg=SomePackage',
            'svn+svn://svn.repo/some_pkg/trunk/#egg=SomePackage',
            'https://example.com/foo/foo-0.26.0-py2.py3-none-any.whl',
            'http://my.package.repo/SomePackage-1.0.4.zip',
        ],

        # various 'pip install' options
        'c.d': [
            '-i https://pypi.org/simple',
            '--extra-index-url http://my.package.repo/simple',
            '--no-clean',
            '-e svn+http://svn.example.com/svn/MyProject/trunk@2019#egg=MyProject',
        ]
    }

    expected = [
        'git+https://git.repo/some_pkg.git#egg=SomePackage',
        'svn+svn://svn.repo/some_pkg/trunk/#egg=SomePackage',
        'https://example.com/foo/foo-0.26.0-py2.py3-none-any.whl',
        'http://my.package.repo/SomePackage-1.0.4.zip',
        '-i https://pypi.org/simple',
        '--extra-index-url http://my.package.repo/simple',
        '--no-clean',
        '-e svn+http://svn.example.com/svn/MyProject/trunk@2019#egg=MyProject',
    ]

    assert filter_requirements(reqs) == expected


def test_excluded_system_requirements():
    reqs = {
        'a.b': [
            'libxml2-dev [platform:dpkg]',
            'dev-libs/libxml2',
            'python3-lxml [(platform:redhat platform:base-py3)]',
            'foo [platform:bar]',
        ],
        'c.d': [
            '# python is in EXCLUDED_REQUIREMENTS',
            'python [platform:brew] ==3.7.3',
            'libxml2-dev [platform:dpkg]',
            'python3-all-dev [platform:dpkg !platform:ubuntu-precise]',
        ],
        'user': [
            'foo',   # should never exclude from user reqs
        ]
    }

    excluded = ['python3-lxml', 'foo']

    expected = [
        'libxml2-dev [platform:dpkg]  # from collection a.b',
        'dev-libs/libxml2  # from collection a.b',
        'libxml2-dev [platform:dpkg]  # from collection c.d',
        'python3-all-dev [platform:dpkg !platform:ubuntu-precise]  # from collection c.d',
        'foo  # from collection user',
    ]

    assert filter_requirements(reqs, exclude=excluded, is_python=False) == expected


def test_excluded_python_requirements():
    reqs = {
        "a.b": [
            "req1",
            "req2==0.1.0",
            "req4 ; python_version<='3.9'",
            "git+https://git.repo/some_pkg.git#egg=SomePackage",
        ],
        "c.d": [
            "req1<=2.0.0",
            "req3",
        ],
        "user": [
            "req1"   # should never exclude from user reqs
        ]
    }

    excluded = [
        "req1",
        "req4",
        "git",
    ]

    expected = [
        "req2==0.1.0  # from collection a.b",
        "git+https://git.repo/some_pkg.git#egg=SomePackage",
        "req3  # from collection c.d",
        "req1  # from collection user",
    ]

    assert filter_requirements(reqs, excluded) == expected


def test_filter_requirements_excludes_collections():
    """
    Test that excluding all requirements from a list of collections works in filter_requirements().
    """
    reqs = {
        "a.b": [
            "req1",
            "req2==0.1.0",
            "req4 ; python_version<='3.9'",
            "git+https://git.repo/some_pkg.git#egg=SomePackage",
        ],
        "c.d": [
            "req1<=2.0.0",
            "req3",
        ],
        "e.f": [
            "req5",
        ],
        "user": [
            "req1"   # should never exclude from user reqs
        ]
    }

    excluded_collections = [
        'a.b',
        'e.f',
    ]

    expected = [
        "req1<=2.0.0  # from collection c.d",
        "req3  # from collection c.d",
        "req1  # from collection user",
    ]

    assert filter_requirements(reqs, exclude_collections=excluded_collections) == expected


def test_requirement_regex_exclusions():
    reqs = {
        "a.b": [
            "foo",
            "shimmy",
            "kungfoo",
            "aaab",
        ],
        "c.d": [
            "foobar",
            "shake",
            "ab",
        ]
    }

    excluded = [
        "Foo",       # straight string comparison (case shouldn't matter)
        "foo.",      # straight string comparison (shouldn't match)
        "~foo.",     # regex (shouldn't match b/c not full string match)
        "~Sh.*",     # regex (case shouldn't matter)
        "~^.+ab",    # regex
    ]

    expected = [
        "kungfoo  # from collection a.b",
        "foobar  # from collection c.d",
        "ab  # from collection c.d"
    ]

    assert filter_requirements(reqs, excluded) == expected


def test_collection_regex_exclusions():
    reqs = {
        "a.b": ["foo"],
        "c.d": ["bar"],
        "ab.cd": ["foobar"],
        "e.f": ["baz"],
        "be.fun": ["foobaz"],
    }

    excluded_collections = [
        r"~A\..+",     # regex (case shouldn't matter)
        "E.F",         # straight string comparison (case shouldn't matter)
        "~b.c",        # regex (shouldn't match b/c not full string match)
    ]

    expected = [
        "bar  # from collection c.d",
        "foobar  # from collection ab.cd",
        "foobaz  # from collection be.fun",
    ]

    assert filter_requirements(reqs, exclude_collections=excluded_collections) == expected


def test_resolve_python_dependencies_constructs_pip_command(mocker, tmp_path):
    report_path = tmp_path / 'nested' / 'report.json'
    requirements = [
        'example[extra]>=1  # from collection example.collection',
        '--no-index',
    ]
    captured_requirements = None

    def pip_run(command, **_kwargs):
        nonlocal captured_requirements
        requirements_path = command[command.index('--requirement') + 1]
        with open(requirements_path, encoding='utf-8') as requirements_file:
            captured_requirements = requirements_file.read()
        generated_report = command[command.index('--report') + 1]
        with open(generated_report, 'w', encoding='utf-8') as report_file:
            json.dump({'install': []}, report_file)

    run = mocker.patch('ansible_builder._target_scripts.introspect.subprocess.run', side_effect=pip_run)

    resolve_python_dependencies(requirements, report_path=str(report_path))

    command = run.call_args.args[0]
    assert command[:4] == [sys.executable, '-m', 'pip', 'install']
    assert '--dry-run' in command
    assert '--ignore-installed' in command
    assert '--report' in command
    assert '--requirement' in command
    assert run.call_args.kwargs == {
        'check': True,
        'text': True,
        'capture_output': True,
        'shell': False,
    }
    assert captured_requirements == '\n'.join(requirements + [''])
    assert report_path.is_file()


def test_resolve_python_dependencies_preserves_report_bytes(mocker, tmp_path):
    report_path = tmp_path / 'report.json'
    original_report = b'{\n  "pip_version": "23.0",\n  "version": "1",\n  "install": []\n}\n'

    def pip_run(command, **_kwargs):
        generated_report = command[command.index('--report') + 1]
        with open(generated_report, 'wb') as report_file:
            report_file.write(original_report)

    mocker.patch('ansible_builder._target_scripts.introspect.subprocess.run', side_effect=pip_run)

    resolve_python_dependencies([], report_path=str(report_path))

    assert report_path.read_bytes() == original_report


def test_resolve_python_dependencies_reports_pip_failure_and_cleans_up(mocker, tmp_path):
    report_path = tmp_path / 'report.json'
    temporary_paths = []

    def pip_run(command, **_kwargs):
        temporary_paths.extend([
            command[command.index('--requirement') + 1],
            command[command.index('--report') + 1],
        ])
        raise subprocess.CalledProcessError(2, command, stderr='No matching distribution found')

    mocker.patch('ansible_builder._target_scripts.introspect.subprocess.run', side_effect=pip_run)

    with pytest.raises(PythonDependencyResolutionError, match='pip exit code 2') as exc_info:
        resolve_python_dependencies(['missing-package'], report_path=str(report_path))

    assert 'No matching distribution found' in str(exc_info.value)
    assert not report_path.exists()
    assert all(not os.path.exists(path) for path in temporary_paths)


def test_extract_transitive_python_dependencies(data_dir):
    report_path = data_dir / 'pip_reports' / 'basic.json'

    assert extract_transitive_python_dependencies(str(report_path)) == [
        'alpha-package',
        'zoo-package',
    ]


@pytest.mark.parametrize(('report', 'message'), [
    ([], 'top-level value must be an object'),
    ({}, "'install' must be a list"),
    ({'install': {}}, "'install' must be a list"),
    ({'install': [None]}, 'install entry 1 must be an object'),
    ({'install': [{}]}, "lacks valid 'metadata'"),
    ({'install': [{'metadata': {}, 'requested': False}]}, "lacks a valid 'metadata.name'"),
    ({'install': [{'metadata': {'name': 'invalid name'}, 'requested': False}]},
     "lacks a valid 'metadata.name'"),
    ({'install': [{'metadata': {'name': 'example'}, 'requested': None}]}, "invalid 'requested' value"),
])
def test_extract_transitive_python_dependencies_rejects_invalid_reports(tmp_path, report, message):
    report_path = tmp_path / 'report.json'
    report_path.write_text(json.dumps(report))

    with pytest.raises(PythonDependencyResolutionError, match=message):
        extract_transitive_python_dependencies(str(report_path))


def test_extract_transitive_python_dependencies_rejects_invalid_json(tmp_path):
    report_path = tmp_path / 'report.json'
    report_path.write_text('{')

    with pytest.raises(PythonDependencyResolutionError, match='Unable to read pip installation report'):
        extract_transitive_python_dependencies(str(report_path))


@pytest.mark.parametrize(('report_requested', 'transitive_requested'), [
    (True, False),
    (False, True),
    (True, True),
])
def test_write_python_dependency_outputs_resolves_once(mocker, tmp_path, report_requested, transitive_requested):
    report_path = str(tmp_path / 'report.json') if report_requested else None
    transitive_path = str(tmp_path / 'transitive.txt') if transitive_requested else None

    def resolve(_requirements, *, report_path):
        with open(report_path, 'w', encoding='utf-8') as report_file:
            json.dump({
                'install': [
                    {'requested': True, 'metadata': {'name': 'direct'}},
                    {'requested': False, 'metadata': {'name': 'Transitive_Dependency'}},
                ],
            }, report_file)

    resolver = mocker.patch(
        'ansible_builder._target_scripts.introspect.resolve_python_dependencies',
        side_effect=resolve,
    )

    write_python_dependency_outputs(
        ['direct'],
        report_path=report_path,
        transitive_path=transitive_path,
    )

    resolver.assert_called_once()
    if transitive_path:
        with open(transitive_path, encoding='utf-8') as transitive_file:
            assert transitive_file.read() == 'transitive-dependency\n'


def test_write_python_dependency_outputs_writes_empty_transitive_file(mocker, tmp_path):
    transitive_path = tmp_path / 'transitive.txt'

    def resolve(_requirements, *, report_path):
        with open(report_path, 'w', encoding='utf-8') as report_file:
            json.dump({'install': []}, report_file)

    mocker.patch(
        'ansible_builder._target_scripts.introspect.resolve_python_dependencies',
        side_effect=resolve,
    )

    write_python_dependency_outputs([], report_path=None, transitive_path=str(transitive_path))

    assert transitive_path.read_text() == ''


def test_run_introspect_dependency_outputs_preserve_existing_output(mocker, tmp_path, capsys):
    dependency_data = {
        'python': {'example.collection': ['direct-package>=1']},
        'system': {'example.collection': ['system-package [platform:rpm]']},
    }

    def process_data(**_kwargs):
        return {
            dependency_type: {source: requirements.copy() for source, requirements in sources.items()}
            for dependency_type, sources in dependency_data.items()
        }

    mocker.patch('ansible_builder._target_scripts.introspect.process', side_effect=process_data)
    dependency_outputs = mocker.patch('ansible_builder._target_scripts.introspect.write_python_dependency_outputs')

    original_pip = tmp_path / 'original-pip.txt'
    original_args = parse_args(['introspect', f'--write-pip={original_pip}'])
    with pytest.raises(SystemExit, match='0'):
        run_introspect(original_args, logging.getLogger(__name__))
    original_stdout = capsys.readouterr().out
    dependency_outputs.assert_not_called()

    resolved_pip = tmp_path / 'resolved-pip.txt'
    report_path = tmp_path / 'report.json'
    transitive_path = tmp_path / 'transitive.txt'
    resolved_args = parse_args([
        'introspect',
        f'--write-pip={resolved_pip}',
        f'--write-python-dependency-report={report_path}',
        f'--write-transitive-python={transitive_path}',
    ])
    with pytest.raises(SystemExit, match='0'):
        run_introspect(resolved_args, logging.getLogger(__name__))
    resolved_stdout = capsys.readouterr().out

    assert resolved_stdout == original_stdout
    assert resolved_pip.read_bytes() == original_pip.read_bytes()
    dependency_outputs.assert_called_once_with(
        ['direct-package>=1  # from collection example.collection'],
        report_path=str(report_path),
        transitive_path=str(transitive_path),
    )
