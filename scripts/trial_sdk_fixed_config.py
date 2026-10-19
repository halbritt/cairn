"""One fixed service/producer configuration projection; no execution authority."""
from trial_sdk_command_policy import FixedCommandPolicy
from trial_sdk_command_service_runtime import RuntimeBinding
from trial_sdk_incoming import canonical_size_sha256


class FixedConfigError(ValueError):
    pass


def require(value, code):
    if not value:
        raise FixedConfigError(code)


def bindings(document):
    require(type(document) is dict and set(document) == {
        'policy', 'engine', 'cwd', 'env', 'path', 'deadline_ns', 'standing_raw',
        'standing_working', 'preparation_raw', 'preparation_working'}, 'fixed_config_shape')
    policy_data = document['policy']
    require(type(policy_data) is dict and set(policy_data) == {
        'executable', 'socket', 'token_file', 'repo', 'task', 'run', 'context'}, 'fixed_policy_shape')
    require(type(policy_data['context']) is dict, 'fixed_policy_context')
    policy = FixedCommandPolicy(**dict(policy_data, context=tuple(policy_data['context'].items())))
    engine = document['engine']
    # This source-owned configuration is intentionally not an arbitrary engine
    # plugin/configuration surface. Preserve all admitted engine options verbatim.
    require(type(engine) is dict and engine.get('harness') == 'claude'
        and engine.get('recall_mode') == 'agent_tools'
        and engine.get('recall_observations') is False
        and not {'inbox_recall_binding', 'cairn', 'socket', 'token_file',
                 'repo', 'task_id', 'run_id', 'context'} & set(engine), 'fixed_engine_config')
    require(type(engine.get('state_dir')) is str and engine['state_dir'].startswith('/'),
            'fixed_engine_state')
    # Credentials and real service routes never enter the producer. The adapter
    # recognizes only these inert names and sends semantic requests over RPC.
    config = dict(engine, cairn='/unavailable/cairn', socket='/unavailable/socket',
                  token_file='/unavailable/token', repo=policy.repo, task_id=policy.task,
                  run_id=policy.run, context=dict(policy.context))
    require(canonical_size_sha256(config)[0] <= 65536, 'fixed_engine_config_bound')
    runtime = RuntimeBinding(policy, document['cwd'], dict(document['env']),
        document['path'], document['deadline_ns'], document['standing_raw'],
        document['standing_working'], document['preparation_raw'], document['preparation_working'])
    return runtime, config
