import sys
import pytest
import os
import yaml

from condorprovider.utils import to_snakecase
from condorprovider.apptainer_cmd_builder import from_kubernetes
from condorprovider.apptainer_cmd_builder.tests._boilerplate import container_output, ValidationStruct

with open(os.path.abspath(__file__).replace('.py', '.yaml')) as ifile:
    test_data = {d['name']: d for d in yaml.safe_load_all(ifile)}

def get_pod_and_validation(test_name: str, use_fake_volumes: bool):
    return (
        from_kubernetes(test_data[test_name]['pod'], test_data[test_name]['containers'], use_fake_volumes),
        ValidationStruct.from_dict(test_data[test_name]['validation']),
    )

def test_to_snakecase():
    examples = {
        'apiVersion': 'api_version',
        'requiredDuringSchedulingIgnoredDuringExecution': 'required_during_scheduling_ignored_during_execution',
        'snake_case': 'snake_case',
        'camelCase': 'camel_case',
        'PascalCase': 'pascal_case',
        'kebab-case': 'kebab_case',
    }
    for any_case, snake_case in examples.items():
        assert to_snakecase(any_case) == snake_case


@pytest.mark.parametrize("test_name", test_data.keys())
def test_k8s_import(test_name):
    builder, validation = get_pod_and_validation(test_name, use_fake_volumes=False)

    with container_output(builder, test_name=test_name) as output:
        print(output, file=sys.stderr)
        validation.raise_on_conditions(output)

@pytest.mark.parametrize("test_name", [k for k in test_data.keys() if test_data[k]['can_use_fake_volumes']])
def test_k8s_import_with_fake_volumes(test_name):
    builder, validation = get_pod_and_validation(test_name, use_fake_volumes=True)

    with container_output(builder, test_name=test_name) as output:
        print(output, file=sys.stderr)
        validation.raise_on_conditions(output)
