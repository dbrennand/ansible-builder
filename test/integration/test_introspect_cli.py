import json
import zipfile

import pytest


def build_test_wheel(wheel_directory, name, *, dependencies=()):
    wheel_name = name.replace('-', '_').lower()
    distribution_info = f'{wheel_name}-1.0.dist-info'
    wheel_path = wheel_directory / f'{wheel_name}-1.0-py3-none-any.whl'
    metadata = [
        'Metadata-Version: 2.1',
        f'Name: {name}',
        'Version: 1.0',
    ]
    metadata.extend(f'Requires-Dist: {dependency}' for dependency in dependencies)
    files = {
        f'{wheel_name}/__init__.py': '',
        f'{distribution_info}/METADATA': '\n'.join(metadata + ['', '']),
        f'{distribution_info}/WHEEL': '\n'.join([
            'Wheel-Version: 1.0',
            'Generator: ansible-builder integration tests',
            'Root-Is-Purelib: true',
            'Tag: py3-none-any',
            '',
        ]),
    }
    files[f'{distribution_info}/RECORD'] = ''.join(f'{filename},,\n' for filename in files)

    with zipfile.ZipFile(wheel_path, 'w') as wheel:
        for filename, content in files.items():
            wheel.writestr(filename, content)


@pytest.fixture(name='local_dependency_graph')
def fixture_local_dependency_graph(tmp_path):
    wheel_directory = tmp_path / 'wheels'
    wheel_directory.mkdir()
    build_test_wheel(wheel_directory, 'Direct-Package', dependencies=('Transitive-One', 'transitive-two'))
    build_test_wheel(wheel_directory, 'Transitive-One')
    build_test_wheel(wheel_directory, 'transitive-two', dependencies=('transitive-three',))
    build_test_wheel(wheel_directory, 'transitive-three')

    collection_root = tmp_path / 'collections'
    collection = collection_root / 'ansible_collections' / 'test' / 'dependency_graph'
    collection.mkdir(parents=True)
    (collection / 'galaxy.yml').write_text('namespace: test\nname: dependency_graph\n')
    (collection / 'requirements.txt').write_text('Direct-Package\n')

    return collection_root, wheel_directory


def test_introspect_write_bindep(cli, data_dir, tmp_path):
    dest_file = tmp_path / 'req.txt'
    cli(f'ansible-builder introspect {data_dir} --write-bindep={dest_file}')

    assert dest_file.read_text() == '\n'.join([
        'subversion [platform:rpm]  # from collection test.bindep',
        'subversion [platform:dpkg]  # from collection test.bindep',
        '',
    ])


def test_introspect_write_python(cli, data_dir, tmp_path):
    dest_file = tmp_path / 'req.txt'
    cli(f'ansible-builder introspect {data_dir} --write-pip={dest_file}')

    assert dest_file.read_text() == '\n'.join([
        'pyvcloud>=14  # from collection test.metadata',
        'pytz  # from collection test.reqfile',
        'python-dateutil>=2.8.2  # from collection test.reqfile',
        'jinja2>=3.0  # from collection test.reqfile',
        'tacacs_plus  # from collection test.reqfile',
        'pyvcloud>=18.0.10  # from collection test.reqfile',
        '',
    ])


def test_introspect_with_user_reqs(cli, data_dir, tmp_path):
    user_file = tmp_path / 'requirements.txt'
    user_file.write_text("ansible\npytest\n")
    pip_out = tmp_path / 'pip-output.txt'

    cli(f'ansible-builder introspect --user-pip={user_file} --write-pip={pip_out} {data_dir}')

    pip_data = pip_out.read_text()
    assert 'pytz  # from collection test.reqfile' in pip_data
    # 'ansible' allowed in user requirements
    assert 'ansible  # from collection user' in pip_data
    # 'pytest' allowed in user requirements
    assert 'pytest  # from collection user' in pip_data


def test_introspect_exclude_python(cli, data_dir, tmp_path):
    exclude_file = tmp_path / 'exclude.txt'
    exclude_file.write_text("pytz\npython-dateutil\n")
    pip_out = tmp_path / 'pip-output.txt'

    cli(f'ansible-builder introspect {data_dir} --exclude-pip-reqs={exclude_file} --write-pip={pip_out}')

    pip_data = pip_out.read_text()
    assert 'pytz' not in pip_data
    assert 'python-dateutil' not in pip_data


def test_introspect_exclude_system(cli, data_dir, tmp_path):
    exclude_file = tmp_path / 'exclude.txt'
    exclude_file.write_text("subversion\n")
    sys_out = tmp_path / 'sys-output.txt'

    cli(f'ansible-builder introspect {data_dir} --exclude-bindep-reqs={exclude_file} --write-bindep={sys_out}')

    # Everything was excluded, so there should be no output file.
    assert not sys_out.exists()


def test_introspect_exclude_collections(cli, data_dir, tmp_path):
    exclude_file = tmp_path / 'exclude.txt'
    exclude_file.write_text("test.reqfile\ntest.bindep\n")
    pip_out = tmp_path / 'pip-output.txt'

    cli(f'ansible-builder introspect {data_dir} --exclude-collection-reqs={exclude_file} --write-pip={pip_out}')

    pip_data = pip_out.read_text()
    assert 'from collection test.reqfile' not in pip_data
    assert 'from collection test.bindep' not in pip_data


@pytest.mark.parametrize('write_report', [True, False])
def test_introspect_writes_transitive_python_dependencies(
    cli,
    local_dependency_graph,
    monkeypatch,
    tmp_path,
    write_report,
):
    collection_root, wheel_directory = local_dependency_graph
    monkeypatch.setenv('PIP_NO_INDEX', '1')
    monkeypatch.setenv('PIP_FIND_LINKS', str(wheel_directory))

    direct_path = tmp_path / 'direct.txt'
    report_path = tmp_path / 'report.json'
    transitive_path = tmp_path / 'transitive.txt'
    command = (
        f'ansible-builder introspect --write-pip={direct_path} '
        f'--write-transitive-python={transitive_path} '
    )
    if write_report:
        command += f'--write-python-dependency-report={report_path} '
    command += str(collection_root)

    baseline = cli(f'ansible-builder introspect {collection_root}')
    result = cli(command)

    assert result.stdout == baseline.stdout
    assert direct_path.read_text() == 'Direct-Package  # from collection test.dependency_graph\n'
    assert transitive_path.read_text() == '\n'.join([
        'transitive-one',
        'transitive-three',
        'transitive-two',
        '',
    ])

    if write_report:
        report = json.loads(report_path.read_text())
        requested_by_name = {
            item['metadata']['name'].lower(): item['requested']
            for item in report['install']
        }
        assert requested_by_name == {
            'direct-package': True,
            'transitive-one': False,
            'transitive-three': False,
            'transitive-two': False,
        }
    else:
        assert not report_path.exists()


def test_introspect_reports_python_resolution_failure(cli, local_dependency_graph, monkeypatch, tmp_path):
    collection_root, wheel_directory = local_dependency_graph
    requirements_path = (
        collection_root / 'ansible_collections' / 'test' / 'dependency_graph' / 'requirements.txt'
    )
    requirements_path.write_text('missing-package==1.0\n')
    monkeypatch.setenv('PIP_NO_INDEX', '1')
    monkeypatch.setenv('PIP_FIND_LINKS', str(wheel_directory))
    report_path = tmp_path / 'report.json'
    transitive_path = tmp_path / 'transitive.txt'

    result = cli(
        f'ansible-builder introspect '
        f'--write-python-dependency-report={report_path} '
        f'--write-transitive-python={transitive_path} '
        f'{collection_root}',
        allow_error=True,
    )

    assert result.rc != 0
    assert '---\n' in result.stdout
    assert 'missing-package==1.0  # from collection test.dependency_graph' in result.stdout
    assert 'Transitive Python dependency resolution failed' in result.stdout
    assert 'No matching distribution found for missing-package==1.0' in result.stdout
    assert not report_path.exists()
    assert not transitive_path.exists()
