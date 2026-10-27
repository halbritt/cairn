"""Resolve explicit inbox bindings before suppression, source reads, or recall."""
import hashlib
import json
import os
from pathlib import Path
import uuid


SCHEMA = 'cairn.inbox-recall-binding/2'


def _owner_manifest(config):
    path = Path(config['inbox_recall_binding'])
    if not path.is_absolute() or path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077:
        raise ValueError('inbox recall binding must be owner-only')
    manifest = json.loads(path.read_text())
    return path, manifest


def _checked_file(spec):
    path = Path(spec['path'])
    if not path.is_absolute() or path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o022:
        raise ValueError('inbox recall requires owner-controlled absolute paths')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != spec['sha256']:
        raise ValueError('inbox recall binding changed')
    return path, raw


class InboxBinding:
    """Binding policy over one complete lifecycle bundle and its grant ledger."""

    def __init__(self, bundle):
        self._bundle = bundle
        self._ledger = bundle.load('grant_ledger')

    def effective_config(self, config, event, is_wake):
        """Snapshot the one-file activation decision for this hook invocation.

        Disabled staging must not inspect pins whose configurations are still being
        replaced. Removing the opt-in from a private copy preserves every old path.
        """
        if not config.get("inbox_recall_binding"):
            return config
        excluded = (any(os.environ.get(k) == "1" for k in
                        ("CAIRN_LIFECYCLE_DISABLED", "CAIRN_LIFECYCLE_CHILD", "CAIRN_COORDINATION_DISABLED"))
                    or bool(os.environ.get("CAIRN_WAKE_CONTEXT")))
        if event is not None:
            excluded = excluded or event.get("hook_event_name") not in ("SessionStart", "UserPromptSubmit")
            for name in ("cwd", "project_path"):
                if event.get(name) and isinstance(event[name], str):
                    root = Path(event[name]).resolve()
                    excluded = excluded or any((p / marker).exists() for p in (root, *root.parents)
                                               for marker in (".cairn-no-memory", ".cairn-no-coordination"))
        if excluded:
            result = dict(config)
            result.pop("inbox_recall_binding")
            return result
        _, manifest = _owner_manifest(config)
        if manifest.get('schema') != SCHEMA or type(manifest.get('enabled')) is not bool:
            raise ValueError('unsupported inbox recall activation state')
        enabled = manifest["enabled"]
        if (event is not None and event.get("hook_event_name") == "UserPromptSubmit"
                and is_wake(event)):
            enabled = self.load_bridge(config, validate=False).activation_decision(config, event, enabled)
        if enabled:
            return config
        result = dict(config)
        result.pop("inbox_recall_binding")
        return result

    def load_bridge(self, config, *, validate=True):
        """Execute only the complete, explicitly pinned inbox recall bundle."""
        _, manifest = _owner_manifest(config)
        if manifest.get('schema') != SCHEMA:
            raise ValueError('unsupported inbox recall binding; restage its complete lifecycle bundle')
        module = self._bundle.load_pinned(manifest["bridge"], "inbox_recall")
        if validate:
            module.binding(config)
        return module

    def validate(self, config, *, require_enabled=False):
        path, manifest = _owner_manifest(config)
        if manifest.get('schema') != SCHEMA or type(manifest.get('enabled')) is not bool:
            raise ValueError('unknown inbox recall binding')
        if require_enabled and not manifest['enabled']:
            raise ValueError('inbox recall binding is disabled')
        # The role snapshots own code validation; _checked_file is for configuration bytes.
        self._bundle.check_pinned(manifest['bridge'], 'inbox_recall')
        memory = json.loads(_checked_file(manifest['memory_config'])[1])
        descriptors = manifest.get('coordination_configs')
        if not isinstance(descriptors, list) or not 1 <= len(descriptors) <= 8:
            raise ValueError('one to eight explicit coordination configurations required')
        coordinators = [json.loads(_checked_file(item)[1]) for item in descriptors]
        profiles = [memory, *coordinators]
        homes = [str(Path(c['config_home']).resolve()) if c.get('config_home') else None for c in coordinators]
        if len(coordinators) > 1 and (None in homes or len(homes) != len(set(homes))):
            raise ValueError('shared memory binding requires distinct explicit configuration homes')
        if (config.get('harness') not in ('codex', 'claude') or memory.get('recall_mode') != 'agent_tools'
                or any(not c.get('native_delivery') for c in coordinators)
                or any(c.get('inbox_recall_binding') != str(path) for c in profiles)
                or any(c.get(k) != memory.get(k) for c in profiles for k in ('harness', 'cairn', 'socket', 'token_file', 'repo'))
                or config not in profiles or not all(Path(c['state_dir']).is_absolute() for c in profiles)
                or any(Path(memory['state_dir']).resolve() == Path(c['state_dir']).resolve() for c in coordinators)
                or any(c.get('config_home') and not Path(c['config_home']).is_absolute() for c in coordinators)
                or type(memory.get('context_bytes')) is not int or not 1000 <= memory['context_bytes'] <= 9500):
            raise ValueError('incompatible inbox recall profile binding')
        engine = self._bundle.load_pinned(manifest['engine'], 'memory')
        if getattr(engine, 'INBOX_RECALL_VERSION', None) != 1:
            raise ValueError('pinned memory engine lacks the inbox recall contract')
        return engine, memory

    def activation_decision(self, config, event, enabled):
        """One decision for both reserved-wake hooks; no grant or active turn changes."""
        if config.get('harness') not in ('codex', 'claude') or event.get('hook_event_name') != 'UserPromptSubmit':
            return enabled
        session = event.get('session_id')
        turn = self._ledger.claude_owner(event) if config['harness'] == 'claude' else event.get('turn_id')
        try:
            if str(uuid.UUID(session)) != session or (config['harness'] == 'codex' and str(uuid.UUID(turn)) != turn):
                return enabled
        except (ValueError, TypeError, AttributeError):
            return enabled
        # Privacy exclusions do not write per-session activation metadata.
        for name in ('cwd', 'project_path'):
            if event.get(name) and any((p / '.cairn-no-memory').exists() for p in
                    [Path(event[name]).resolve(), *Path(event[name]).resolve().parents]):
                return enabled
        manifest = json.loads(Path(config['inbox_recall_binding']).read_text())
        path = Path(manifest['memory_config']['path'])
        # Disabled staging intentionally does not require the final configuration
        # hash yet. It reads only the owner-controlled location of the existing ledger.
        if not path.is_absolute() or path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o022:
            raise ValueError('activation ledger configuration is not owner-controlled')
        memory = json.loads(path.read_text())
        if not isinstance(memory, dict) or not isinstance(memory.get('state_dir'), str):
            raise ValueError('activation ledger configuration lacks a state directory')
        return self._ledger.activation_decision(memory, config['harness'], session, turn, enabled)

    def coordinates_wake(self, config, event):
        """A coordination opt-out leaves the independent required-only hook active."""
        manifest = json.loads(Path(config['inbox_recall_binding']).read_text())
        coordinators = [json.loads(_checked_file(item)[1]) for item in manifest['coordination_configs']]
        variable = 'CLAUDE_CONFIG_DIR' if config['harness'] == 'claude' else 'CODEX_HOME'
        active = Path(os.environ.get(variable, str(Path.home() / ('.' + config['harness'])))).resolve()
        matches = [c for c in coordinators if not c.get('config_home') or active == Path(c['config_home']).resolve()]
        if len(matches) > 1:
            raise ValueError('ambiguous coordination profile')
        if not matches:
            return False
        coordination = matches[0]
        if os.environ.get('CAIRN_COORDINATION_DISABLED') == '1' or os.environ.get('CAIRN_WAKE_CONTEXT'):
            return False
        root = Path(event['cwd']).resolve()
        if any((p / '.cairn-no-coordination').exists() for p in [root, *root.parents]):
            return False
        if coordination.get('config_home'):
            variable = 'CLAUDE_CONFIG_DIR' if config['harness'] == 'claude' else 'CODEX_HOME'
            active = Path(os.environ.get(variable, str(Path.home() / ('.' + config['harness'])))).resolve()
            if active != Path(coordination['config_home']).resolve():
                return False
        return True
