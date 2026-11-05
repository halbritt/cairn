"""Real WC02 browser journeys, loaded only by the fixture's explicit wc02 scope."""
import hashlib
import json
import uuid


def artifact_fixture(base, uid, dump, require):
    class ArtifactFixture(base):
        states = ("artifact_workflow", "artifact_empty", "artifact_unavailable", "artifact_disabled", "artifact_races")

        def item_count(self, name):
            return 55 if name in ("artifact_workflow", "artifact_races") else 2

        def artifact(self, *args, expected="OK"):
            return self.work("artifact", *args, expected=expected)

        def links(self, item=None):
            return self.artifact("list", "--project", self.slug, "--item", item or self.item_id,
                                 "--include-removed")["artifacts"]

        def new_result(self, label):
            return self.host.agent("sender", "create", dict(request_id=str(uuid.uuid4()), draft=dict(
                kind="note", body="ARTIFACT_SOURCE_BODY_NEVER_RENDERED " + label, claim_type="self",
                sensitivity="local", scope=dict(repo=self.collection, task_id="*", run_id="*"))))

        def seed(self, name):
            super().seed(name)
            self.guard_item_id = uid(name + ":item:1")
            self.work("features", "enable", "--feature", "work-artifacts/1")
            self.artifact_case = {}
            if name == "artifact_empty":
                return
            if name == "artifact_unavailable":
                record = self.new_result("forgotten")
                revision = self.artifact("register", "--kind", "cairn_result", "--record", record["record_id"],
                    "--version", str(record["version"]), "--display-name", "FORGOTTEN_ARTIFACT_NAME")
                link = self.artifact("link", "--project", self.slug, "--item", self.item_id,
                    "--revision", revision["revision_id"], "--role", "evidence", "--rationale", "FORGOTTEN_ARTIFACT_REASON")
                grant = self.host.operator("bootstrap", dict(request_id=str(uuid.uuid4()), reason="Disposable artifact source deletion"))
                preview = self.host.operator("preview-delete", args=[record["record_id"]])
                self.host.operator("forget", dict(request_id=str(uuid.uuid4()), record_id=record["record_id"],
                    expected_version=record["version"], grant_id=grant["grant_id"], preview_id=preview["preview_id"]))
                current = self.artifact("show", "--revision", revision["revision_id"])
                require(current.get("unavailable") is True, "forgotten artifact source remains available")
                require("FORGOTTEN_ARTIFACT" not in json.dumps(current) + json.dumps(self.links()),
                        "forgotten copied artifact fields remain readable")
                self.artifact_case = dict(revision=revision, link=link)
                return
            older = self.artifact("register", "--kind", "report", "--url", "https://reports.fixture.invalid/selected",
                "--revision", "selected-v1", "--display-name", 'Selected <img src=x onerror="window.artifactInjected=true"> ' + "long-report-" * 25)
            newer = self.artifact("register", "--kind", "report", "--url", "https://reports.fixture.invalid/selected",
                "--revision", "selected-v2")
            require(older["artifact_id"] == newer["artifact_id"] and older["revision_id"] != newer["revision_id"],
                    "report versions did not preserve canonical artifact identity")
            retained = self.artifact("show", "--revision", older["revision_id"])
            require(retained["revision_id"] == older["revision_id"] and retained["revision_label"] == "selected-v1",
                    "older exact revision silently followed the newer registration")
            link = self.artifact("link", "--project", self.slug, "--item", self.item_id,
                "--revision", older["revision_id"], "--role", "input", "--rationale", "Selected fixture input")
            self.artifact_case = dict(revision=older, newer=newer, link=link)
            if name in ("artifact_workflow", "artifact_races"):
                for index in range(1, 55):
                    self.artifact("link", "--project", self.slug, "--item", uid(f"{name}:item:{index}"),
                                  "--revision", older["revision_id"], "--role", "input")
                self.artifact("link", "--project", self.slug, "--item", self.item_id,
                              "--revision", older["revision_id"], "--role", "evidence")
            if name == "artifact_workflow":
                # All history transitions use the real CLI, not table inserts.
                for index in range(26):
                    self.artifact("unlink", "--project", self.slug, "--item", self.item_id,
                        "--link-id", link["link_id"], "--expected-revision", str(link["revision"]))
                    link = self.artifact("link", "--project", self.slug, "--item", self.item_id,
                        "--revision", older["revision_id"], "--role", "input", "--rationale", f"Selected history {index + 1}")
                self.artifact_case["link"] = link
                require(link["revision"] == 53, "real link transitions did not retain 53 revisions")
                self.session = self.register()
                self.work("associate", "--project", self.slug, "--item", self.item_id, "--target-type", "session",
                          "--target-id", self.session["agent_id"] + ":" + self.session["execution_id"])
                self.work("blocker", "add", "--project", self.slug, "--item", self.item_id,
                          "--text", "Selected artifact impact blocker")
            else:
                extra = self.artifact("register", "--kind", "report", "--url", "https://reports.fixture.invalid/retained",
                                      "--revision", "retained-v1")
                self.artifact("link", "--project", self.slug, "--item", self.item_id,
                              "--revision", extra["revision_id"], "--role", "output")
                self.artifact_case["retained"] = extra
                if name == "artifact_disabled":
                    self.work("features", "disable", "--feature", "work-artifacts/1")
                else:
                    self.artifact_case["other"] = self.artifact("register", "--kind", "report",
                        "--url", "https://reports.fixture.invalid/unaffected", "--revision", "unaffected-v1")

        def headers(self, page):
            session = page.request.get(self.url + "/api/auth/session")
            require(session.ok and session.json().get("authenticated") is True, "artifact proof lacks operator session")
            return {"Origin": self.url, "X-CSRF-Token": session.json()["csrf_token"]}

        def guarded_unlink(self, page, link):
            variants = (("missing item", {"item_uuid": None}, 400, "INVALID_REQUEST"),
                ("invalid item", {"item_uuid": "invalid"}, 400, "INVALID_REQUEST"),
                ("wrong item", {"item_uuid": self.guard_item_id}, 404, "NOT_FOUND"),
                ("missing revision", {"expected_revision": None}, 400, "INVALID_REQUEST"),
                ("zero revision", {"expected_revision": 0}, 400, "INVALID_REQUEST"),
                ("stale revision", {"expected_revision": link["revision"] + 1}, 409, "VERSION_CONFLICT"))
            for label, changes, status, code in variants:
                body = dict(request_id=str(uuid.uuid4()), project=self.slug, item_uuid=self.item_id,
                            link_id=link["link_id"], expected_revision=link["revision"])
                for key, value in changes.items():
                    if value is None:
                        body.pop(key)
                    else:
                        body[key] = value
                response = page.request.post(self.url + "/api/work/artifact/unlink", data=body, headers=self.headers(page))
                retained = next(row for row in self.links() if row["link_id"] == link["link_id"])
                require(retained == link, "refused " + label + " changed the exact artifact link")
                require(response.status == status and response.json().get("code") == code,
                        "artifact unlink " + label + " returned " + response.text()[:300])
                self.report["checks"].append(dict(name="artifact unlink refuses " + label, category="acceptance", status="passed"))

        def browser_register(self, page, kind, viewport):
            from playwright.sync_api import expect
            details = page.locator("#artifact-register-details")
            if not details.get_attribute("open") == "":
                details.locator("summary").click()
            page.locator("#artifact-kind").select_option(kind)
            page.locator("#artifact-display-name").fill(f"Browser {viewport} {kind}")
            if kind != "cairn_result":
                page.locator("#artifact-revision-label").fill(viewport + "-v1")
            fields = {}
            if kind == "git":
                fields = {"artifact-git-repo": f"https://git.fixture.invalid/{viewport}",
                          "artifact-git-commit": hashlib.sha1(viewport.encode()).hexdigest()}
            elif kind == "report":
                fields = {"artifact-report-locator": f"https://reports.fixture.invalid/{viewport}",
                          "artifact-report-digest": "sha256:" + hashlib.sha256(viewport.encode()).hexdigest()}
                page.locator("#artifact-report-provenance").select_option("asserted")
            elif kind == "ci":
                fields = {"artifact-ci-provider": "synthetic-ci", "artifact-ci-repo": f"https://git.fixture.invalid/{viewport}",
                          "artifact-ci-run": viewport + "-run", "artifact-ci-attempt": "2", "artifact-ci-status": "reported-success"}
            else:
                result = self.new_result(viewport)
                fields = {"artifact-result-id": result["record_id"], "artifact-result-version": str(result["version"])}
            for field, value in fields.items():
                page.locator("#" + field).fill(value)
            revision = self.browser_write(page, "/api/work/artifact/register", lambda: page.locator("#artifact-register-submit").click())
            expect(page.locator("#item-artifact-notice")).to_contain_text(revision["revision_id"])
            expect(page.locator("#artifact-link-revision")).to_have_value(revision["revision_id"])
            retained = self.artifact("show", "--revision", revision["revision_id"])
            require(retained["artifact"]["kind"] == kind, "browser registration did not persist the selected kind")
            require(not any(link["revision_id"] == revision["revision_id"] for link in self.links()),
                    "registration unexpectedly created a work link")
            return revision

        def browser_link(self, page, revision, role, lost_response=False):
            from playwright.sync_api import expect
            page.locator("#artifact-link-revision").fill(revision["revision_id"])
            page.locator("#artifact-link-role").select_option(role)
            page.locator("#artifact-link-rationale").fill("Selected browser reason " + role)
            intercepted = []
            if lost_response:
                def drop_after_commit(route):
                    response = route.fetch()
                    require(response.ok, "lost-response fixture did not reach successful real handler")
                    intercepted.append((route.request.post_data_json, response.json()))
                    route.abort("connectionclosed")
                page.route(self.url + "/api/work/artifact/link", drop_after_commit, times=1)
                page.locator("#artifact-link-submit").click()
                expect(page.locator("#item-artifact-error")).to_contain_text("Change not confirmed")
                require(len(intercepted) == 1, "intended real committed response was not dropped")
            with page.expect_response(lambda response: response.url == self.url + "/api/work/artifact/link"
                                      and response.request.method == "POST") as pending:
                page.locator("#artifact-link-submit").click()
            response = pending.value
            require(response.ok, "browser artifact link failed: " + response.text()[:300])
            link = response.json()
            row = page.locator(f'.artifact-link-item[data-link-id="{link["link_id"]}"]')
            expect(row).to_be_visible()
            retained = next(entry for entry in self.links() if entry["link_id"] == link["link_id"])
            require(retained["revision_id"] == revision["revision_id"] and retained["role"] == role
                    and retained["state"] == "active" and retained["rationale"] == "Selected browser reason " + role,
                    "browser link did not persist exact revision, role and selected reason")
            if lost_response:
                require(response.request.post_data_json == intercepted[0][0], "manual retry changed request UUID or intent")
                require(link["link_id"] == intercepted[0][1]["link_id"] and link["revision"] == intercepted[0][1]["revision"],
                        "lost-response retry created a second link revision")
                self.report["checks"].append(dict(name="artifact link retries exact request after real committed response loss",
                    category="acceptance", status="passed"))
            return retained

        def workflow(self, page, viewport):
            from playwright.sync_api import expect
            self.activate_card_by_keyboard(page, self.item_id)
            expect(page.locator("#artifact-link-submit")).to_be_enabled()
            for index, kind in enumerate(("git", "report", "ci", "cairn_result")):
                revision = self.browser_register(page, kind, viewport)
                link = self.browser_link(page, revision, ("input", "output", "evidence", "evidence")[index],
                                         lost_response=kind == "report" and viewport == "desktop")
                if kind == "git":
                    if viewport == "desktop":
                        self.guarded_unlink(page, link)
                    row = page.locator(f'.artifact-link-item[data-link-id="{link["link_id"]}"]')
                    self.browser_write(page, "/api/work/artifact/unlink", lambda: row.locator(".unlink-artifact-btn").click())
                    expect(row).to_have_count(0)
                    page.locator("#artifact-include-removed").check()
                    expect(row).to_have_attribute("data-state", "removed")
                    require(next(entry for entry in self.links() if entry["link_id"] == link["link_id"])["state"] == "removed",
                            "browser unlink was not persisted")
                row = page.locator(f'.artifact-link-item[data-link-id="{link["link_id"]}"]')
                row.locator(".inspect-artifact-btn").click()
                details = page.locator("#artifact-revision-details")
                expect(details).to_contain_text(revision["revision_id"])
                identity = (revision.get("git_commit_oid") if kind == "git" else revision["artifact"].get("report_locator")
                    if kind == "report" else revision["artifact"].get("ci_run_id") if kind == "ci"
                    else revision["artifact"].get("cairn_record_id"))
                require(identity, "registered exact kind identity is missing")
                expect(details).to_contain_text(identity)
                if kind == "cairn_result":
                    expect(details.locator("a")).to_have_count(0)
                else:
                    locator = revision["artifact"].get("report_locator") or revision["artifact"].get("git_repo") or revision["artifact"].get("ci_repo")
                    expect(details.locator("a")).to_have_attribute("href", locator)
                page.locator("#artifact-return-item").click()
                expect(page.locator("#detail-modal")).to_be_visible()
            require("ARTIFACT_SOURCE_BODY_NEVER_RENDERED" not in page.content(), "artifact UI fetched/rendered result body")
            old = self.artifact_case["revision"]
            row = page.locator(f'.artifact-link-item[data-link-id="{self.artifact_case["link"]["link_id"]}"]')
            row.locator(".artifact-history-btn").click()
            expect(page.locator("#artifact-history li")).to_have_count(50)
            first = page.locator("#artifact-history li").evaluate_all("rows => rows.map(row => row.dataset.historyId)")
            page.locator("#artifact-history-next").click()
            expect(page.locator("#artifact-history-page")).to_contain_text("Page 2")
            later = page.locator("#artifact-history li").evaluate_all("rows => rows.map(row => row.dataset.historyId)")
            require(len(later) == 3 and set(first).isdisjoint(later), "history continuation lost/repeated revisions")
            page.locator("#artifact-history-first").click()
            expect(page.locator("#artifact-history li")).to_have_count(50)
            page.screenshot(path=str(self.output / ("artifact-history-" + viewport + ".png")), full_page=True)
            page.locator("#artifact-history-close").click()
            row.locator(".inspect-artifact-btn").click()
            expect(page.locator("#artifact-revision-details")).to_contain_text("selected-v1")
            require(old["revision_id"] in page.url and self.artifact_case["newer"]["revision_id"] not in page.url,
                    "artifact navigation followed a newer revision")
            expect(page.locator(".artifact-impact-item")).to_have_count(50)
            first = page.locator(".artifact-impact-item").evaluate_all("rows => rows.map(row => row.dataset.linkId)")
            page.locator("#artifact-impact-next").click()
            expect(page.locator("#artifact-impact-page")).to_contain_text("Page 2")
            later = page.locator(".artifact-impact-item").evaluate_all("rows => rows.map(row => row.dataset.linkId)")
            require(len(later) == 6 and set(first).isdisjoint(later), "impact paging repeated/lost item-role rows")
            require(page.evaluate("window.artifactInjected === undefined") and page.locator("#artifact-revision-details img").count() == 0,
                    "selected artifact metadata became executable markup")
            page.locator("#artifact-impact-first").click()
            expect(page.locator(".artifact-impact-item")).to_have_count(50)
            page.screenshot(path=str(self.output / ("artifact-revision-" + viewport + ".png")), full_page=True)
            page.locator("#artifact-return-item").click()
            expect(page.locator("#detail-modal")).to_be_visible()
            expect(page.locator("#modal-meta")).to_contain_text(self.item_id)

        def state_checks(self, page, name):
            from playwright.sync_api import expect
            page.locator(f'.work-card[data-item-uuid="{self.item_id}"]').click()
            if name == "artifact_empty":
                expect(page.locator("#item-artifacts-empty")).to_be_visible()
                expect(page.locator(".artifact-link-item")).to_have_count(0)
                expect(page.locator("#artifact-link-submit")).to_be_enabled()
                return
            if name == "artifact_unavailable":
                expect(page.locator(".artifact-availability").first).to_contain_text("unavailable")
                require("FORGOTTEN_ARTIFACT" not in page.content(), "forgotten artifact metadata rendered")
                page.locator(".artifact-history-btn").first.click()
                expect(page.locator("#artifact-history li")).to_have_count(1)
                require("FORGOTTEN_ARTIFACT" not in page.content(), "history revived forgotten reason")
                page.locator(".inspect-artifact-btn").first.click()
                expect(page.locator("#artifact-revision-details .artifact-availability")).to_contain_text("unavailable")
                require("FORGOTTEN_ARTIFACT" not in page.content(), "exact revision revived forgotten metadata")
                require(page.locator("#artifact-revision-details a").count() == 0, "unavailable source retained navigation")
                return
            expect(page.locator("#artifact-admission")).to_contain_text("disabled")
            expect(page.locator("#artifact-register-submit")).to_be_disabled()
            expect(page.locator("#artifact-link-submit")).to_be_disabled()
            link = self.artifact_case["link"]
            row = page.locator(f'.artifact-link-item[data-link-id="{link["link_id"]}"]')
            expect(row).to_be_visible()
            self.browser_write(page, "/api/work/artifact/unlink", lambda: row.locator(".unlink-artifact-btn").click())
            page.locator("#artifact-include-removed").check()
            expect(row).to_have_attribute("data-state", "removed")
            row.locator(".artifact-history-btn").click()
            expect(page.locator("#artifact-history li")).to_have_count(2)
            row.locator(".inspect-artifact-btn").click()
            expect(page.locator("#artifact-revision-details")).to_contain_text("selected-v1")
            expect(page.locator("#artifact-page-admission")).to_contain_text("disabled")
            page.once("dialog", lambda dialog: dialog.accept())
            self.browser_write(page, "/api/work/artifact/purge", lambda: page.locator("#artifact-purge-btn").click())
            expect(page.locator("#artifact-notice")).to_contain_text("Purged local metadata")
            self.artifact("show", "--revision", self.artifact_case["revision"]["revision_id"], expected="NOT_FOUND")
            require(not any(entry["artifact_id"] == self.artifact_case["revision"]["artifact_id"] for entry in self.links()),
                    "purged artifact links remain readable")
            retained = self.artifact("show", "--revision", self.artifact_case["retained"]["revision_id"])
            require(retained["revision_id"] == self.artifact_case["retained"]["revision_id"], "maintenance erased an unrelated artifact")

        def current_read_races(self, page):
            from playwright.sync_api import expect
            old = self.artifact_case["revision"]
            page.goto(self.url + "/?artifact_revision=" + old["revision_id"])
            expect(page.locator("#artifact-revision-details")).to_contain_text("selected-v1")
            expect(page.locator("#artifact-impact-next")).to_be_enabled()
            self.artifact("purge", "--artifact", old["artifact_id"])
            with page.expect_response(lambda response: "/api/work/artifact/impact?" in response.url) as pending:
                page.locator("#artifact-impact-next").click()
            require(pending.value.status == 404 and pending.value.json().get("code") == "NOT_FOUND",
                    "purged current impact read did not refuse the exact revision")
            expect(page.locator("#artifact-error")).to_contain_text("Could not read reverse impact")
            expect(page.locator("#artifact-revision-details")).to_be_empty()
            expect(page.locator("#artifact-purge-btn")).to_be_disabled()
            require("https://reports.fixture.invalid/selected" not in page.content(),
                    "refused current read retained the old report locator")

            def inspect(revision):
                page.locator("#artifact-lookup-id").fill(revision["revision_id"])
                page.locator("#artifact-lookup-form button").click()
                expect(page.locator("#artifact-revision-details")).to_contain_text(revision["revision_id"])
                expect(page.locator("#artifact-impact-page")).to_contain_text("Page 1")

            other, target = self.artifact_case["other"], self.artifact_case["retained"]
            inspect(other)
            inspect(target)
            delivered = []

            def reply_after_navigation(route):
                response = route.fetch()
                require(response.ok and response.json().get("artifact_id") == target["artifact_id"],
                        "pending purge did not commit its exact real target")
                # Browser Back is available even while application buttons are
                # disabled. Forward the actual response after that navigation.
                page.evaluate("history.back()")
                page.wait_for_url("**artifact_revision=" + other["revision_id"])
                delivered.append(response.json())
                route.fulfill(response=response)

            page.route(self.url + "/api/work/artifact/purge", reply_after_navigation, times=1)
            page.once("dialog", lambda dialog: dialog.accept())
            self.browser_write(page, "/api/work/artifact/purge", lambda: page.locator("#artifact-purge-btn").click())
            require(len(delivered) == 1, "real delayed purge reply was not exercised")
            expect(page.locator("#artifact-revision-details")).to_contain_text(other["revision_id"])
            expect(page.locator("#artifact-notice")).to_be_hidden()
            require(other["revision_id"] in page.url, "late purge reply replaced the selected artifact route")
            self.artifact("show", "--revision", target["revision_id"], expected="NOT_FOUND")
            require(self.artifact("show", "--revision", other["revision_id"])["revision_id"] == other["revision_id"],
                    "navigation changed the purge mutation target")

            before = self.host.sql("SELECT count(*) FROM cairn.work_artifact_revision")
            body = dict(request_id=str(uuid.uuid4()), kind="report", report_locator="https://reports.fixture.invalid/invalid-trailing",
                        revision_label="must-not-register")
            response = page.request.post(self.url + "/api/work/artifact/register", data=json.dumps(body) + "}",
                headers=dict(self.headers(page), **{"Content-Type": "application/json"}))
            require(response.status == 400 and response.json().get("code") == "INVALID_REQUEST",
                    "trailing closing delimiter was accepted by the artifact mutation parser")
            require(self.host.sql("SELECT count(*) FROM cairn.work_artifact_revision") == before,
                    "malformed trailing-body request retained a registration")

        def prepare_manifest(self, name, manifest):
            for state in manifest["surfaces"][name]["states"].values():
                for action in state.get("actions", []):
                    if action.get("selector") == ".work-card >> nth=0":
                        action["selector"] = f'.work-card[data-item-uuid="{self.item_id}"]'
            if name == "artifact_workflow":
                states = manifest["surfaces"][name]["states"]
                states["exact_revision"]["route"] = "/?artifact_revision=" + self.artifact_case["revision"]["revision_id"]
                states["link_history"]["actions"][1]["selector"] = f'.artifact-link-item[data-link-id="{self.artifact_case["link"]["link_id"]}"] .artifact-history-btn'
            elif name == "artifact_unavailable":
                manifest["surfaces"][name]["states"]["retained_unavailable"]["route"] = "/?artifact_revision=" + self.artifact_case["revision"]["revision_id"]
            elif name == "artifact_races":
                manifest["surfaces"][name]["states"]["unaffected_revision"]["route"] = "/?artifact_revision=" + self.artifact_case["other"]["revision_id"]

        def run_state(self, browser, name):
            self.seed(name)
            self.start_web()
            with browser.new_context(viewport=dict(width=1440, height=1000)) as context:
                unchanged = self.coordination_state()
                page = self.login(context)
                storage = context.storage_state()
                if name == "artifact_workflow":
                    for viewport, size in (("desktop", dict(width=1440, height=1000)), ("phone", dict(width=390, height=844))):
                        with browser.new_context(viewport=size, storage_state=storage) as journey:
                            ui = journey.new_page()
                            ui.set_default_timeout(7000)
                            ui.goto(self.url)
                            self.check("artifact browser journey " + viewport, lambda: self.workflow(ui, viewport), ui)
                else:
                    page.set_default_timeout(7000)
                    if name == "artifact_races":
                        self.check("artifact current-read and delayed-purge races", lambda: self.current_read_races(page), page)
                    else:
                        self.check("artifact state " + name, lambda: self.state_checks(page, name), page)
                self.check("UIPass " + name, lambda: self.uipass(name, storage))
                self.check("artifact view preserves coordination " + name,
                    lambda: require(self.coordination_state() == unchanged, "artifact browser changed sessions/events/deliveries"))

    return ArtifactFixture
