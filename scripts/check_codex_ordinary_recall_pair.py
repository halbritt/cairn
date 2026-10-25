"""Capture full and compact ordinary-hook context in a local Codex Responses request.

The fixture runs the production memory renderer with a frozen synthetic source,
returns that renderer output from a UserPromptSubmit command hook, and captures
Codex's first request at a loopback-only Responses endpoint. The endpoint stops
before inference. It records no raw prompt or response bodies.
"""
from __future__ import annotations

import hashlib
import http.server
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading


ROOT = Path(__file__).resolve().parents[1]
FROZEN = ROOT / "trials/ordinary-recall/codex-luna-2026-10-25/FROZEN.json"
MODEL = "gpt-6-luna"
MODEL_CLI = shutil.which("codex")
STOP_STATUS = 418
KNOWN_ZERO = (
    "CONTEXT_MISSING CURRENTNESS_MISMATCH OUTSIDE_VALIDITY CLASS_NOT_CONSEQUENTIAL "
    "AUTHORITY_INACTIVE POLICY_UNENFORCEABLE OPEN_CONFLICT ATTRIBUTION_UNRECONCILED "
    "EVIDENCE_UNAVAILABLE NO_LEXICAL_MATCH REDUNDANT NO_FAILURE_MATCH NO_ENTITY_MATCH"
).split()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)


def _renderer():
    sys.path.insert(0, str(ROOT / "scripts"))
    from test_eager_candidate_context import hook
    return hook


def _render(hook, frozen, view):
    source = frozen["model_request"]["source"]
    body = source["body"]
    body_bytes = body.encode("utf-8")
    record = dict(record_id=source["record_id"], version=source["version"],
                  **{"class": source["class"]}, kind=source["kind"],
                  lifecycle="active", sensitivity="shareable", attribution_state="self",
                  body=body, body_sha256=digest(body_bytes), source_extent="whole_source",
                  scope=dict(repository="fixture:cairn219", task="ordinary-recall-pair", run="pair-20261025"),
                  witness="synthetic fixture", written_at="2026-10-25T00:00:00Z")
    entry = dict(record_id=source["record_id"], version=source["version"],
                 **{"class": source["class"]}, summary="Synthetic settlement retry guidance.",
                 body_sha256=record["body_sha256"],
                 summary_span=dict(offset=0, length=len("Synthetic settlement retry guidance.".encode())),
                 match_span=dict(offset=0, length=len(body_bytes)),
                 pull_arguments=dict(receipt_id="00000000-0000-4000-8000-000000000219",
                                     request_id="00000000-0000-4000-8000-000000000220",
                                     handle="00000000-0000-4000-8000-000000000221"))
    candidate = dict(pull_arguments=entry["pull_arguments"],
                     response=dict(selection=dict(mandatory=False, record=record,
                                                  evidence=[], authority=[], reason="eligible fixture source"),
                                   competing=[]))
    class Observer:
        config = {"recall_view": view}
    omitted = dict.fromkeys(KNOWN_ZERO, 0)
    omitted["OPTIONAL_BUDGET"] = 0
    result = dict(status="READY", index=[entry], omitted=omitted,
                  discovery=dict(state="ready", coverage=dict(indexed=1, eligible=1)))
    inspection = dict(pull_calls=1, delivered_records=1, remaining_pull_calls=3, refusals={})
    rendered = hook.render_agent_candidates([], result, 9500, dict(rejected={}),
                                            [candidate], inspection, observer=Observer())
    start = rendered.find('{"selected":')
    require(start >= 0, "renderer output did not contain its model-facing JSON view")
    decoded = json.loads(rendered[start:])
    delivered = decoded["candidate_bodies"][0]
    actual = delivered["response"]["selection"]["record"]
    require(actual["record_id"] == source["record_id"] and actual["version"] == source["version"],
            "renderer changed the source identity")
    require(actual["body"] == body and actual["body_sha256"] == digest(body_bytes),
            "renderer changed or excerpted the frozen source")
    require(actual["source_extent"] == "whole_source", "renderer lost the whole-source extent")
    require(delivered["pull_arguments"] == entry["pull_arguments"], "renderer changed pull arguments")
    if view == "compact/1":
        require(decoded.get("schema") == "cairn.hook-recall-compact/1", "compact schema missing")
        require(decoded["candidate_search"]["omitted"] == omitted, "compact diagnostics changed")
    else:
        require("schema" not in decoded, "full view unexpectedly gained compact schema")
        require(not any(key in decoded["candidate_search"]["omitted"] for key in KNOWN_ZERO),
                "full-view zero omission filtering changed")
    return rendered, dict(context_sha256=digest(rendered.encode()), body_sha256=actual["body_sha256"],
                          body_bytes=len(body_bytes), record_id=actual["record_id"],
                          version=actual["version"], pull_arguments=delivered["pull_arguments"],
                          compact_schema=decoded.get("schema"), explicit_zero_count=sum(
                              value == 0 for value in decoded["candidate_search"]["omitted"].values()
                              if type(value) is int))


def _sse_response():
    # Return a completed, explicitly synthetic answer. It never claims model inference.
    item = dict(type="message", id="msg_local_fixture", role="assistant", status="completed",
                content=[dict(type="output_text", text="Provider-free transport fixture; no inference.", annotations=[])])
    response = dict(id="resp_local_fixture", object="response", created_at=0,
                    status="in_progress", output=[], model=MODEL)
    events = [dict(type="response.created", response=response),
              dict(type="response.output_item.added", output_index=0, item=dict(item, status="in_progress")),
              dict(type="response.content_part.added", item_id=item["id"], output_index=0,
                   content_index=0, part=dict(type="output_text", text="")),
              dict(type="response.output_text.delta", item_id=item["id"], output_index=0,
                   content_index=0, delta=item["content"][0]["text"]),
              dict(type="response.output_text.done", item_id=item["id"], output_index=0,
                   content_index=0, text=item["content"][0]["text"]),
              dict(type="response.content_part.done", item_id=item["id"], output_index=0,
                   content_index=0, part=item["content"][0]),
              dict(type="response.output_item.done", output_index=0, item=item),
              dict(type="response.completed", response=dict(response, status="completed", output=[item],
                   usage=dict(input_tokens=1, output_tokens=1, total_tokens=2)))]
    return "".join("event: " + event["type"] + "\ndata: " +
                   json.dumps(dict(event, sequence_number=i)) + "\n\n"
                   for i, event in enumerate(events)).encode()


def _arm(root: Path, frozen: dict, view: str, context: str):
    arm = root / view.replace("/", "-")
    home, work = arm / "codex-home", arm / "work"
    hooks = home / "hooks.json"
    hook_script = arm / "emit_hook_context.py"
    context_file = arm / "hook-context.txt"
    home.mkdir(parents=True, mode=0o700)
    work.mkdir(mode=0o700)
    context_file.write_text(context, encoding="utf-8")
    hook_script.write_text(
        "import json, pathlib, sys\n"
        "text = pathlib.Path(sys.argv[1]).read_text(encoding='utf-8')\n"
        "print(json.dumps({'hookSpecificOutput': {'hookEventName': 'UserPromptSubmit', "
        "'additionalContext': text}}, ensure_ascii=False))\n", encoding="utf-8")
    hook_script.chmod(0o700)
    hooks.write_text(json.dumps({"hooks": {"UserPromptSubmit": [{"hooks": [{
        "type": "command", "command": shlex.join([sys.executable, str(hook_script), str(context_file)]),
        "timeout": 5, "additionalContextLimit": 9500
    }]}]}}), encoding="utf-8")
    hooks.chmod(0o600)

    captured = []
    failures = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass
        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
                require(self.path == "/v1/responses" and 0 < length <= 2_000_000,
                        "unexpected fixture HTTP request")
                require(not captured, "Codex retried the local fixture request")
                request = json.loads(self.rfile.read(length))
                require(request.get("model") == MODEL, "Codex changed the requested model alias")
                captured.append(request)
                self.send_response(200)
                payload = _sse_response()
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except (AssertionError, KeyError, TypeError, ValueError) as exc:
                failures.append(type(exc).__name__ + ": " + str(exc))
                self.send_error(500)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    config = (
        f'model = "{MODEL}"\nmodel_reasoning_effort = "{frozen["model_request"]["reasoning_effort"]}"\n'
        'model_provider = "fixture"\ncheck_for_update_on_startup = false\n'
        '[features]\nhooks = true\n'
        '[model_providers.fixture]\nname = "Local CAIRN-219 fixture"\n'
        f'base_url = "http://127.0.0.1:{server.server_port}/v1"\n'
        'wire_api = "responses"\nrequires_openai_auth = false\n'
        'request_max_retries = 0\nstream_max_retries = 0\n'
    )
    (home / "config.toml").write_text(config, encoding="utf-8")
    (home / "config.toml").chmod(0o600)
    env = {key: os.environ[key] for key in ("PATH", "LANG", "TERM") if key in os.environ}
    env.update(HOME=str(home), CODEX_HOME=str(home),
               HTTP_PROXY="http://127.0.0.1:1", HTTPS_PROXY="http://127.0.0.1:1",
               ALL_PROXY="http://127.0.0.1:1", NO_PROXY="127.0.0.1,localhost")
    prompt = frozen["model_request"]["task"]
    command = [MODEL_CLI, "exec", "--ephemeral", "--json", "--dangerously-bypass-hook-trust",
               "--skip-git-repo-check", "--cd", str(work), "--sandbox", "read-only",
               "--model", MODEL, "--disable", "code_mode_only", "--disable", "code_mode",
               "--disable", "code_mode_host", "--disable", "shell_tool", "--", prompt]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = subprocess.run(command, env=env, cwd=work, capture_output=True,
                                text=True, timeout=45)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    require(result.returncode == 0, "Codex local fixture did not complete its synthetic response: " +
            result.stderr[-500:])
    require(not failures and len(captured) == 1, f"fixture captured {len(captured)} requests: {failures}")
    request = captured[0]
    actual_effort = request.get("reasoning", {}).get("effort")
    require(actual_effort == frozen["model_request"]["reasoning_effort"],
            "Codex did not send frozen reasoning effort")
    strings = list(_strings(request.get("input")))
    matches = [value for value in strings if context in value]
    require(matches, "rendered UserPromptSubmit context was not present in model request input")
    require(any(frozen["model_request"]["task"] in value for value in strings),
            "frozen task text was not present in model request input")
    source = frozen["model_request"]["source"]
    require(any(source["body"] in value for value in strings), "complete source body missing from request")
    require(source["record_id"] in json.dumps(request.get("input")), "source record ID missing from request")
    require(str(source["version"]) in json.dumps(request.get("input")), "source version missing from request")
    return dict(view=view, response_count=1, model=MODEL, reasoning_effort=actual_effort,
                user_prompt_submit_context_in_request=True, exact_rendered_hook_context_matches=True,
                source_body_present=True, source_id_present=True, source_version_present=True,
                local_responses_requests=1, provider_inference=False, answer=None, usage=None,
                context_sha256=digest(context.encode()), input_context_bytes=len(context.encode()))


def check():
    require(MODEL_CLI is not None, "Codex CLI unavailable")
    frozen_bytes = FROZEN.read_bytes()
    frozen = json.loads(frozen_bytes)
    require(frozen.get("schema") == "cairn.ordinary-recall-pair/1", "frozen fixture schema mismatch")
    hook = _renderer()
    with tempfile.TemporaryDirectory(prefix="cairn219-ordinary-pair-") as tmp:
        root = Path(tmp)
        rendered = {}
        renderer_receipts = {}
        for view in ("full", "compact/1"):
            rendered[view], renderer_receipts[view] = _render(hook, frozen, view)
        require(rendered["full"] != rendered["compact/1"], "full and compact renderings did not differ")
        receipts = [_arm(root, frozen, view, rendered[view]) for view in ("full", "compact/1")]
        for receipt in receipts:
            source_receipt = renderer_receipts[receipt["view"]]
            require(receipt["context_sha256"] == source_receipt["context_sha256"],
                    "request did not contain exact renderer bytes")
        require(renderer_receipts["full"]["body_sha256"] == renderer_receipts["compact/1"]["body_sha256"],
                "paired arms did not preserve source body checksum")
        require(renderer_receipts["full"]["record_id"] == renderer_receipts["compact/1"]["record_id"] and
                renderer_receipts["full"]["version"] == renderer_receipts["compact/1"]["version"],
                "paired arms did not preserve source identity")
        return dict(schema="cairn.ordinary-recall-pair-fixture-result/1", result="PASS",
                    fixture_sha256=digest(frozen_bytes), rubric_sha256=digest(json.dumps(
                        frozen["answer_rubric"], sort_keys=True, separators=(",", ":")).encode()),
                    requested_model=MODEL, reasoning_effort=frozen["model_request"]["reasoning_effort"],
                    task_sha256=digest(frozen["model_request"]["task"].encode()),
                    arms=receipts, source=renderer_receipts["full"],
                    paired_source_identity_equal=True, paired_source_body_equal=True,
                    presentation_differs=True, provider_calls=0, provider_usage=None,
                    task_outcome="ungraded; no model inference")


if __name__ == "__main__":
    print(json.dumps(check(), sort_keys=True))
