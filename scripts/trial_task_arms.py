"""Pinned arm preparation for the existing prospective native executor."""
import hashlib
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


def validate_arms(document):
    arms, components = document['arms'], document['memory_arms']
    if not isinstance(arms,list) or not arms or len(set(arms)) != len(arms) or not isinstance(components,dict):
        raise ValueError('explicit unique arms required')
    if set(components) != set(arms)-{'none','direct'}:
        raise ValueError('each memory arm requires its own explicit pins/configuration')
    for label, arm in components.items():
        if not isinstance(label,str) or not label.replace('-','').replace('_','').isalnum():
            raise ValueError('invalid arm name')
        if set(arm)-{'binary','hook','bridge','api_revision','recall_revision','recall_mode','semantic_fallback','embedding_worker'}:
            raise ValueError('unsupported arm configuration')
        if arm['recall_mode'] not in ('ambient','agent_tools') or type(arm['semantic_fallback']) is not bool:
            raise ValueError('explicit supported recall configuration required')
        for key in ('api_revision','recall_revision'):
            if not isinstance(arm.get(key),str) or not arm[key]:
                raise ValueError('both API and recall revision provenance required')
        checked_file(arm['binary']); checked_file(arm['hook'])
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
        target = output/'components'/label
        target.mkdir(parents=True)
        row = dict(arm)
        for name, basename in (('binary','cairn'),('hook','memory.py'),('bridge','inbox_recall.py')):
            if name not in arm:
                continue
            source = checked_file(arm[name])
            dest = target/basename
            shutil.copy2(source,dest)
            dest.chmod(0o700 if name == "binary" else 0o600)
            if hashlib.sha256(dest.read_bytes()).hexdigest() != arm[name]['sha256']:
                raise ValueError('component changed during copy')
            row[name] = str(dest)
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


def bind_ordinary_claude(base, config, bridge_path):
    """Only memory hook callbacks: no coordinator hook, agent or inbox admission."""
    root = Path('/tmp/trial')
    config['recall_mode'] = 'agent_tools'
    config['inbox_recall_binding'] = str(root/'binding.json')
    coordination = {key:config[key] for key in ('harness','cairn','socket','token_file','repo')}
    coordination.update(native_delivery=True, state_dir=str(root/'unused-coordination-state'),
                        inbox_recall_binding=config['inbox_recall_binding'])
    (base/'coordination-config.json').write_text(json.dumps(coordination))
    (base/'hook-config.json').write_text(json.dumps(config))
    def descriptor(actual, native):
        return dict(path=str(native),sha256=hashlib.sha256(actual.read_bytes()).hexdigest())
    manifest = dict(schema='cairn.inbox-recall-binding/1',enabled=True,
                    bridge=descriptor(Path(bridge_path),root/'hook/inbox_recall.py'),
                    engine=descriptor(Path(bridge_path).with_name('memory.py'),root/'hook/memory.py'),
                    memory_config=descriptor(base/'hook-config.json',root/'hookconfig.json'),
                    coordination_configs=[descriptor(base/'coordination-config.json',root/'coordination-config.json')])
    (base/'binding.json').write_text(json.dumps(manifest))
    for name in ('binding.json','coordination-config.json','hook-config.json'):
        (base/name).chmod(0o600)
    return [(base/'binding.json',root/'binding.json','ro'),
            (base/'coordination-config.json',root/'coordination-config.json','ro')]
