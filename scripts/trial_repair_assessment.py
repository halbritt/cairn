"""Keep bounded experiment transport failures separate from artifact checks."""


def repair_assessment(process_exit, gate, events, relay_report):
    if process_exit != 0:
        quota_error = any(e.get('type') == 'error' and e.get('error', {}).get('name') == 'APIError'
                          and e['error'].get('data', {}).get('statusCode') == 429 for e in events)
        limits = relay_report.get('limits', {})
        local_quota = (quota_error and relay_report.get('rejection_codes', {}).get('429', 0) > 0
                       and len(relay_report.get('requests', [])) == limits.get('requests'))
        return dict(task_outcome='unknown', failure_domain='binding' if local_quota else 'unknown',
                    failure_kind='quota' if local_quota else '',
                    reason='Observed local trial request allowance exhausted; candidate checks remain separate.' if local_quota
                    else 'Run exited nonzero without a classified binding cause; candidate checks remain separate.')
    passed = gate['passed']
    return dict(task_outcome='accepted' if passed else 'rejected', failure_domain='none' if passed else 'task',
                failure_kind='', reason='Completed run evaluated against frozen artifact checks; no general memory-benefit claim.')
