"""Pinned arm preparation for the existing prospective native executor."""
import ast
import uuid
import hashlib
import importlib.util
from functools import cache
import json
from pathlib import Path
import shutil


def checked_file(item):
    if not isinstance(item,dict) or set(item) != {'path','sha256'}:
        raise ValueError('component requires exact path and sha256')
    path = Path(item['path'])
    if not path.is_absolute() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
        raise ValueError('arm component missing or changed')
    return path


@cache
def _bundle_validator():
    spec = importlib.util.spec_from_file_location('trial_bundle_validator',
        Path(__file__).resolve().parents[1] / 'integrations/lifecycle/lifecycle_bundle.py')
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)
    return validator


def source_literal(raw, name):
    tree = ast.parse(raw)
    values = [node.value.value for node in tree.body if isinstance(node, ast.Assign)
              and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)
              and isinstance(node.value, ast.Constant)]
    if len(values) > 1:
        raise ValueError('ambiguous lifecycle source declaration')
    return values[0] if values else None


def binding_schema(raw):
    schema = source_literal(raw, 'SCHEMA')
    if schema in (None, 'cairn.inbox-recall-binding/1'):
        return 'cairn.inbox-recall-binding/1'
    if schema != 'cairn.inbox-recall-binding/2':
        raise ValueError('unsupported pinned recall binding schema')
    return schema


def checked_runtime_bundle(hook_path, bundle_pin, bridge_path=None):
    """Read only an explicitly selected installed closure; never stage new code."""
    validator = _bundle_validator()
    hook_raw = validator._read(hook_path)
    bridge_raw = validator._read(bridge_path) if bridge_path is not None else None
    version = source_literal(hook_raw, 'LIFECYCLE_BUNDLE_VERSION')
    schema = binding_schema(bridge_raw) if bridge_raw is not None else None
    modern = version is not None or schema == 'cairn.inbox-recall-binding/2'
    if not modern:
        if bundle_pin is not None:
            raise ValueError('legacy recall source does not declare a lifecycle bundle')
        return None
    if type(version) is not int or version != 1:
        raise ValueError('unsupported pinned lifecycle bundle version')
    if schema is not None and schema != 'cairn.inbox-recall-binding/2':
        raise ValueError('lifecycle bundle requires a version2 recall bridge')
    if bundle_pin is None:
        raise ValueError('current recall source requires an explicit lifecycle bundle pin')
    if (not isinstance(bundle_pin, dict) or set(bundle_pin) != {'path', 'sha256'}
            or not isinstance(bundle_pin['path'], str) or not isinstance(bundle_pin['sha256'], str)):
        raise ValueError('component requires exact path and sha256')
    root = Path(hook_path).parent
    sidecar = Path(bundle_pin['path'])
    if not sidecar.is_absolute() or sidecar != root / 'lifecycle-bundle.json':
        raise ValueError('lifecycle bundle pin must name the selected adjacent sidecar')
    raw = validator._read(sidecar, controlled=True)
    if hashlib.sha256(raw).hexdigest() != bundle_pin['sha256']:
        raise ValueError('lifecycle bundle sidecar changed')
    descriptor = json.loads(raw)
    sources = validator.checked_sources(root, descriptor)
    if source_literal(sources['lifecycle_bundle.py'], 'INSTALLED') is not True:
        raise ValueError('trial requires an explicitly staged installed lifecycle bundle')
    if (Path(hook_path).name != 'memory.py' or hook_raw != sources['memory.py']
            or (bridge_path is not None and (Path(bridge_path) != root / 'inbox_recall.py'
                                            or bridge_raw != sources['inbox_recall.py']))):
        raise ValueError('lifecycle bundle entrypoints must match the selected directory')
    if binding_schema(sources['inbox_recall.py']) != 'cairn.inbox-recall-binding/2':
        raise ValueError('lifecycle bundle requires a version2 recall bridge')
    return descriptor, dict(sources, **{'lifecycle-bundle.json': raw})


def validate_arms(document):
    arms, components = document['arms'], document['memory_arms']
    if not isinstance(arms,list) or not arms or len(set(arms)) != len(arms) or not isinstance(components,dict):
        raise ValueError('explicit unique arms required')
    if set(components) != set(arms)-{'none','direct'}:
        raise ValueError('each memory arm requires its own explicit pins/configuration')
    for label, arm in components.items():
        if not isinstance(label,str) or not label.replace('-','').replace('_','').isalnum():
            raise ValueError('invalid arm name')
        if set(arm)-{'binary','hook','bridge','api_revision','recall_revision','recall_mode','semantic_fallback','embedding_worker','bundle'}:
            raise ValueError('unsupported arm configuration')
        if arm['recall_mode'] not in ('ambient','agent_tools') or type(arm['semantic_fallback']) is not bool:
            raise ValueError('explicit supported recall configuration required')
        for key in ('api_revision','recall_revision'):
            if not isinstance(arm.get(key),str) or not arm[key]:
                raise ValueError('both API and recall revision provenance required')
        checked_file(arm['binary']); checked_file(arm['hook'])
        closure = checked_runtime_bundle(Path(arm['hook']['path']), arm.get('bundle'),
                                         Path(arm['bridge']['path']) if 'bridge' in arm else None)
        if closure is not None:
            for role, name in (('hook', 'memory.py'), ('bridge', 'inbox_recall.py')):
                if role in arm and hashlib.sha256(closure[1][name]).hexdigest() != arm[role]['sha256']:
                    raise ValueError('selected lifecycle bundle differs from its entrypoint pin')
        if arm['recall_mode'] == 'agent_tools':
            checked_file(arm['bridge'])
        elif arm['semantic_fallback']:
            raise ValueError('prospective hosted selector route not admitted; use ambient lexical or agent_tools')
        if arm['semantic_fallback']:
            checked_file(arm['embedding_worker'])
        elif 'embedding_worker' in arm:
            raise ValueError('unused semantic worker is not supported')
    if "baseline" in components and components["baseline"]["api_revision"] != document["baseline_commit"]:
        raise ValueError("baseline revision must equal its explicit API pin")
    return arms, components


def copy_arms(document, output, version):
    arms, components = validate_arms(document)
    result = {}
    for label in arms:
        if label not in components:
            continue
        arm = components[label]
        closure = checked_runtime_bundle(Path(arm['hook']['path']), arm.get('bundle'),
                                         Path(arm['bridge']['path']) if 'bridge' in arm else None)
        if closure is not None:
            for role, name in (('hook', 'memory.py'), ('bridge', 'inbox_recall.py')):
                if role in arm and hashlib.sha256(closure[1][name]).hexdigest() != arm[role]['sha256']:
                    raise ValueError('selected lifecycle bundle differs from its entrypoint pin')
        target = output/'components'/label
        target.mkdir(parents=True)
        row = dict(arm)
        for name, basename in (('binary','cairn'),('hook','memory.py'),('bridge','inbox_recall.py')):
            if name not in arm:
                continue
            dest = target/basename
            if closure is not None and name in ('hook', 'bridge'):
                dest.write_bytes(closure[1][basename])
            else:
                shutil.copy2(checked_file(arm[name]), dest)
            dest.chmod(0o700 if name == "binary" else 0o600)
            if hashlib.sha256(dest.read_bytes()).hexdigest() != arm[name]['sha256']:
                raise ValueError('component changed during copy')
            row[name] = str(dest)
        if closure is not None:
            for name, raw in closure[1].items():
                destination = target / name
                destination.write_bytes(raw)
                destination.chmod(0o600)
            row['bundle'] = dict(path=str(target / 'lifecycle-bundle.json'), sha256=arm['bundle']['sha256'])
        build = version(row['binary'])
        if build.get('vcs_revision') != arm['api_revision'] or build.get('vcs_modified') is not False:
            raise ValueError('binary does not attest declared clean API revision')
        row['observed_build'] = build
        # A worker may have adjacent runtime/model dependencies. Its launcher is
        # pinned, not copied away from those dependencies; they remain explicit limits.
        if 'embedding_worker' in arm:
            row['embedding_worker'] = str(checked_file(arm['embedding_worker']))
        result[label] = row
    return arms, result


def bind_ordinary_claude(base, config, bridge_path, *, launcher_task=False, bundle_pin=None):
    """Only memory hook callbacks: no coordinator hook, agent or inbox admission."""
    closure = checked_runtime_bundle(Path(bridge_path).with_name('memory.py'), bundle_pin, bridge_path)
    schema = 'cairn.inbox-recall-binding/2' if closure is not None else 'cairn.inbox-recall-binding/1'
    if launcher_task:
        if 'recall_task_key' in config:
            raise ValueError('launcher task key already assigned')
        tree=ast.parse(closure[1]['memory.py'] if closure is not None else
                       _bundle_validator()._read(Path(bridge_path).with_name('memory.py')))
        versions=[node.value.value for node in tree.body if isinstance(node,ast.Assign)
                  and any(isinstance(t,ast.Name) and t.id=='RECALL_TASK_VERSION' for t in node.targets)
                  and isinstance(node.value,ast.Constant) and type(node.value.value) is int]
        if versions != [1] or config.get('harness') != 'claude':
            raise ValueError('pinned hook does not support launcher task grant version1')
        config['recall_task_key']=str(uuid.uuid4())
    root = Path('/tmp/trial')
    config['recall_mode'] = 'agent_tools'
    config['inbox_recall_binding'] = str(root/'binding.json')
    coordination = {key:config[key] for key in ('harness','cairn','socket','token_file','repo')}
    coordination.update(native_delivery=True, state_dir=str(root/'unused-coordination-state'),
                        inbox_recall_binding=config['inbox_recall_binding'])
    (base/'coordination-config.json').write_text(json.dumps(coordination))
    (base/'hook-config.json').write_text(json.dumps(config))
    def descriptor(actual, native):
        raw = closure[1][actual.name] if closure is not None and actual.name in ('memory.py', 'inbox_recall.py') else actual.read_bytes()
        return dict(path=str(native),sha256=hashlib.sha256(raw).hexdigest())
    manifest = dict(schema=schema,enabled=True,
                    bridge=descriptor(Path(bridge_path),root/'hook/inbox_recall.py'),
                    engine=descriptor(Path(bridge_path).with_name('memory.py'),root/'hook/memory.py'),
                    memory_config=descriptor(base/'hook-config.json',root/'hookconfig.json'),
                    coordination_configs=[descriptor(base/'coordination-config.json',root/'coordination-config.json')])
    if closure is not None:
        manifest['bridge']['bundle'] = closure[0]
        manifest['engine']['bundle'] = closure[0]
    (base/'binding.json').write_text(json.dumps(manifest))
    for name in ('binding.json','coordination-config.json','hook-config.json'):
        (base/name).chmod(0o600)
    return [(base/'binding.json',root/'binding.json','ro'),
            (base/'coordination-config.json',root/'coordination-config.json','ro')]
