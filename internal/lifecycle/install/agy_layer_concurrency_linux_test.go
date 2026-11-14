package install

import (
	"bufio"
	"bytes"
	"context"
	"crypto/sha256"
	"debug/buildinfo"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"
)

// These schedules stop only at the existing outside-action observers. They
// cannot observe inside final check/rename or after the final other-layer read.
type agyConcurrencySchedule struct {
	name, hold                        string
	sameLayer, contended, secondFirst bool
}

func (s agyConcurrencySchedule) MarshalJSON() ([]byte, error) {
	return json.Marshal(struct {
		Name, Hold                        string
		SameLayer, Contended, SecondFirst bool
	}{s.name, s.hold, s.sameLayer, s.contended, s.secondFirst})
}

var agyConcurrencySchedules = []agyConcurrencySchedule{
	{name: "opposite-first-wins"},
	{name: "opposite-second-wins", secondFirst: true},
	{name: "opposite-contended-publication", contended: true},
	{name: "opposite-after-readback", hold: "after:readback"},
	{name: "opposite-after-directory-sync", hold: "after:directory-sync"},
	{name: "same-first-wins", sameLayer: true},
	{name: "same-second-wins", sameLayer: true, secondFirst: true},
	{name: "same-contended-publication", sameLayer: true, contended: true},
}

const agyConcurrencyOld = "{\n  \"opaque\" : 900719925474099312345,\n  \"unicode\" : \"\\ud83d\\ude00\",\n  \"token_file\" : \"/never-open-this-selector\"\n}\n"

type agyConcurrencyFile struct {
	Raw    []byte
	SHA256 string
	Mode   uint32
	UID    uint32
	Links  uint64
	Inode  uint64
	Error  string
}

type agyConcurrencySnapshot struct {
	Paths    map[string]string
	Layers   map[string]agyConcurrencyFile
	Recovery map[string]agyConcurrencyFile
}

type agyConcurrencyPlan struct {
	Plan              AgyLayerPlan
	Name, ImageSHA256 string
}

type agyConcurrencyMessage struct {
	Kind, Point, Name, Time, ImageSHA256 string
	PID                                  int
	Initial                              *agyConcurrencySnapshot
	Selection                            AgyLayerSelection
	Error                                string
}

func agyConcurrencyHash(raw []byte) string {
	sum := sha256.Sum256(raw)
	return hex.EncodeToString(sum[:])
}

func agyConcurrencyRead(path string) agyConcurrencyFile {
	raw, err := os.ReadFile(path)
	if err != nil {
		return agyConcurrencyFile{Error: err.Error()}
	}
	info, err := os.Lstat(path)
	if err != nil {
		return agyConcurrencyFile{Raw: raw, Error: err.Error()}
	}
	stat, ok := info.Sys().(*syscall.Stat_t)
	if !ok {
		return agyConcurrencyFile{Raw: raw, Error: "unknown file identity"}
	}
	return agyConcurrencyFile{Raw: raw, SHA256: agyConcurrencyHash(raw), Mode: uint32(info.Mode().Perm()), UID: stat.Uid, Links: stat.Nlink, Inode: stat.Ino}
}

func agyConcurrencyObserve(plan AgyLayerPlan) agyConcurrencySnapshot {
	view := agyConcurrencySnapshot{Paths: map[string]string{}, Layers: map[string]agyConcurrencyFile{}, Recovery: map[string]agyConcurrencyFile{}}
	for layer, path := range map[string]string{"account": filepath.Join(plan.AccountHome, ".gemini/config/hooks.json"), "project": filepath.Join(plan.Workspace, ".agents/hooks.json")} {
		view.Paths[layer] = path
		view.Layers[layer] = agyConcurrencyRead(path)
		entries, err := os.ReadDir(filepath.Dir(path))
		if err != nil {
			view.Recovery[layer+":directory-error"] = agyConcurrencyFile{Error: err.Error()}
			continue
		}
		for _, entry := range entries {
			if strings.HasPrefix(entry.Name(), ".cairn-agy-") || strings.HasPrefix(entry.Name(), ".cairn-recovery-agy-") {
				view.Recovery[layer+":"+entry.Name()] = agyConcurrencyRead(filepath.Join(filepath.Dir(path), entry.Name()))
			}
		}
	}
	return view
}

// This helper is a separate OS process executing the actual private publisher.
// The only test seam is its existing outside-action observer, never a writer.
func TestAgyLayerConcurrencyHelperProcess(t *testing.T) {
	path := os.Getenv("CAIRN_R06_AGY_CONCURRENCY_HELPER")
	if path == "" {
		return
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var plan agyConcurrencyPlan
	if err := json.Unmarshal(raw, &plan); err != nil {
		t.Fatal(err)
	}
	image, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	imageBytes, err := os.ReadFile(image)
	if err != nil || agyConcurrencyHash(imageBytes) != plan.ImageSHA256 || os.Getpid() <= 1 {
		t.Fatal("helper image/PID differs", err)
	}
	emit := func(message agyConcurrencyMessage) {
		message.PID, message.Name, message.Time = os.Getpid(), plan.Name, time.Now().UTC().Format(time.RFC3339Nano)
		if err := json.NewEncoder(os.Stdout).Encode(message); err != nil {
			t.Fatal(err)
		}
	}
	initial := agyConcurrencyObserve(plan.Plan)
	emit(agyConcurrencyMessage{Kind: "start", ImageSHA256: plan.ImageSHA256, Initial: &initial})
	commands := make(chan string, 1)
	go func() {
		scanner := bufio.NewScanner(os.Stdin)
		for scanner.Scan() {
			commands <- scanner.Text()
		}
		close(commands)
	}()
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	selection, runErr := publishAgyLayer(ctx, plan.Plan, func(point string) error {
		emit(agyConcurrencyMessage{Kind: "boundary", Point: point})
		select {
		case command, ok := <-commands:
			if !ok || command != point {
				return fmt.Errorf("barrier release differs at %s", point)
			}
			return nil
		case <-ctx.Done():
			return ctx.Err()
		}
	})
	message := agyConcurrencyMessage{Kind: "result", Selection: selection}
	if runErr != nil {
		message.Error = runErr.Error()
	}
	emit(message)
	if runErr != nil {
		fmt.Fprintln(os.Stderr, runErr.Error())
		os.Exit(1)
	}
}

type agyConcurrencyOutput struct {
	mu       sync.Mutex
	raw      bytes.Buffer
	pending  []byte
	messages chan agyConcurrencyMessage
}

func (o *agyConcurrencyOutput) Write(raw []byte) (int, error) {
	o.mu.Lock()
	defer o.mu.Unlock()
	o.raw.Write(raw)
	o.pending = append(o.pending, raw...)
	for {
		i := bytes.IndexByte(o.pending, '\n')
		if i < 0 {
			break
		}
		line := append([]byte(nil), o.pending[:i]...)
		o.pending = o.pending[i+1:]
		if len(line) == 0 || bytes.Equal(line, []byte("PASS")) {
			continue
		}
		var message agyConcurrencyMessage
		if err := json.Unmarshal(line, &message); err != nil {
			message = agyConcurrencyMessage{Kind: "protocol-error", Error: string(line)}
		}
		o.messages <- message
	}
	return len(raw), nil
}

func (o *agyConcurrencyOutput) Bytes() []byte {
	o.mu.Lock()
	defer o.mu.Unlock()
	return append([]byte(nil), o.raw.Bytes()...)
}

type agyConcurrencyPeer struct {
	name         string
	command      *exec.Cmd
	input        io.WriteCloser
	output       *agyConcurrencyOutput
	diagnostic   bytes.Buffer
	wait         chan error
	current      agyConcurrencyMessage
	events       []agyConcurrencyMessage
	joined       bool
	exit         int
	cleanupError string
	releases     *[]agyConcurrencyRelease
}

type agyConcurrencyRelease struct {
	Order, PID, BytesWritten int
	Time, Name, Point, Error string
	Raw                      []byte
}

func agyConcurrencyWrite(t *testing.T, path string, value any) {
	t.Helper()
	raw, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, append(raw, '\n'), 0600); err != nil {
		t.Fatal(err)
	}
}

func agyConcurrencyStart(t *testing.T, dir, image, pin string, plan agyConcurrencyPlan, own func(*agyConcurrencyPeer)) *agyConcurrencyPeer {
	t.Helper()
	plan.ImageSHA256 = pin
	planPath := filepath.Join(dir, plan.Name+"-plan.json")
	agyConcurrencyWrite(t, planPath, plan)
	raw, err := os.ReadFile(image)
	if err != nil || agyConcurrencyHash(raw) != pin {
		t.Fatal("helper image changed before use", err)
	}
	p := &agyConcurrencyPeer{name: plan.Name, output: &agyConcurrencyOutput{messages: make(chan agyConcurrencyMessage, 32)}, wait: make(chan error, 1), exit: -1}
	p.command = exec.Command(image, "-test.run=^TestAgyLayerConcurrencyHelperProcess$", "-test.v=false")
	p.command.Env = append(os.Environ(), "CAIRN_R06_AGY_CONCURRENCY_HELPER="+planPath)
	p.command.Stdout, p.command.Stderr = p.output, &p.diagnostic
	p.input, err = p.command.StdinPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err := p.command.Start(); err != nil {
		t.Fatal(err)
	}
	go func() { p.wait <- p.command.Wait() }()
	own(p)
	if p.command.Process.Pid <= 1 {
		t.Fatal("owned child PID must exceed1")
	}
	p.receive(t)
	if p.current.Kind != "start" || p.current.PID != p.command.Process.Pid || p.current.ImageSHA256 != pin {
		t.Fatal("actual helper identity/start mismatch", p.current)
	}
	return p
}

func (p *agyConcurrencyPeer) receive(t *testing.T) {
	t.Helper()
	select {
	case message := <-p.output.messages:
		p.current = message
		p.events = append(p.events, message)
		if len(p.events) > 12 {
			t.Fatal("finite observer message bound exceeded", p.name)
		}
		if message.Kind == "protocol-error" || message.PID != p.command.Process.Pid {
			t.Fatalf("helper protocol/identity: %+v", message)
		}
	case <-time.After(18 * time.Second):
		t.Fatalf("finite helper barrier timed out for %s at %+v", p.name, p.current)
	}
}

func (p *agyConcurrencyPeer) release(t *testing.T) {
	t.Helper()
	if p.current.Kind != "boundary" {
		t.Fatal("release requires current real observer boundary", p.current)
	}
	raw := []byte(p.current.Point + "\n")
	entry := agyConcurrencyRelease{Order: len(*p.releases) + 1, PID: p.command.Process.Pid, Time: time.Now().UTC().Format(time.RFC3339Nano), Name: p.name, Point: p.current.Point, Raw: raw}
	n, err := p.input.Write(raw)
	entry.BytesWritten = n
	if err != nil {
		entry.Error = err.Error()
	}
	*p.releases = append(*p.releases, entry)
	if err != nil || n != len(raw) {
		t.Fatal(err)
	}
}

func (p *agyConcurrencyPeer) advance(t *testing.T, point string) {
	t.Helper()
	for p.current.Kind != "result" {
		if p.current.Kind == "boundary" && p.current.Point == point {
			return
		}
		if p.current.Kind == "boundary" {
			p.release(t)
		}
		p.receive(t)
	}
}

func (p *agyConcurrencyPeer) join(t *testing.T) {
	t.Helper()
	if p.joined {
		return
	}
	_ = p.input.Close()
	select {
	case <-p.wait:
		p.joined = true
		p.exit = p.command.ProcessState.ExitCode()
	case <-time.After(3 * time.Second):
		if p.command.Process.Pid > 1 {
			_ = p.command.Process.Signal(syscall.SIGTERM)
		}
		select {
		case <-p.wait:
			p.joined = true
			p.exit = p.command.ProcessState.ExitCode()
			p.cleanupError = "owned helper required SIGTERM after stdin close"
		case <-time.After(3 * time.Second):
			p.cleanupError = "owned helper did not join; no SIGKILL or group signal sent"
			t.Error(p.cleanupError)
		}
	}
}

type agyConcurrencyPeerRecord struct {
	PID, Exit      int
	Joined         bool
	Stdout, Stderr []byte
	Events         []agyConcurrencyMessage
	CleanupError   string
	Argv           []string
}

type agyConcurrencyCaseRecord struct {
	Name      string
	Plans     []agyConcurrencyPlan
	Prepared  map[string]agyConcurrencyFile
	Snapshots []struct {
		Point string
		View  agyConcurrencySnapshot
	}
	Processes                                              map[string]agyConcurrencyPeerRecord
	TransientDoubleActive, TerminalDoubleActive, BothError bool
	Counterexample                                         string
	Releases                                               []agyConcurrencyRelease
	Limits                                                 []string
}

func agyConcurrencyRecoveryRecord(name string, file agyConcurrencyFile, view agyConcurrencySnapshot) error {
	layer, base, ok := strings.Cut(name, ":")
	token := strings.TrimSuffix(strings.TrimPrefix(base, ".cairn-recovery-agy-"), ".json")
	nonce, err := hex.DecodeString(token)
	if !ok || layer != "account" && layer != "project" || err != nil || len(nonce) != 16 || base != ".cairn-recovery-agy-"+hex.EncodeToString(nonce)+".json" {
		return fmt.Errorf("unknown recovery record name %s", name)
	}
	if file.Error != "" || file.Mode != 0400 || file.UID != uint32(os.Geteuid()) || file.Links != 1 {
		return fmt.Errorf("recovery record metadata differs: %s %+v", name, file)
	}
	var record agyRecoveryRecord
	if err := json.Unmarshal(file.Raw, &record); err != nil {
		return err
	}
	raw, err := json.Marshal(record)
	other := map[string]string{"account": "project", "project": "account"}[layer]
	relative := map[string]string{"account": ".gemini/config/hooks.json", "project": ".agents/hooks.json"}
	if err != nil || !bytes.Equal(raw, file.Raw) || record.Schema != "cairn.agy-layer-recovery/1" || record.Layer != layer || record.Temporary != ".cairn-agy-"+token ||
		record.Selected.Relative != relative[layer] || record.Other.Relative != relative[other] ||
		filepath.Join(record.Selected.Root, record.Selected.Relative) != view.Paths[layer] || filepath.Join(record.Other.Root, record.Other.Relative) != view.Paths[other] ||
		!record.Selected.Present || record.Selected.SHA256 != agyConcurrencyHash([]byte(agyConcurrencyOld)) ||
		record.Selected.File.Mode&07777 != 0640 || record.NewFile.Mode&07777 != 0640 ||
		record.Selected.File.UID != uint32(os.Geteuid()) || record.NewFile.UID != uint32(os.Geteuid()) || record.NewFile.Ino == 0 {
		return fmt.Errorf("recovery record correspondence differs: %s", name)
	}
	// Both inodes can move between selection and temporary names during the
	// controlled exchanges. Match their actual observed bytes, not path guesses.
	old, next := false, false
	for _, files := range []map[string]agyConcurrencyFile{view.Layers, view.Recovery} {
		for _, actual := range files {
			if actual.Error == "" && actual.Mode == 0640 && actual.UID == uint32(os.Geteuid()) && actual.Links == 1 {
				old = old || actual.Inode == record.Selected.File.Ino && actual.SHA256 == record.Selected.SHA256
				next = next || actual.Inode == record.NewFile.Ino && actual.SHA256 == record.NewSHA256
			}
		}
	}
	if !old || !next {
		return fmt.Errorf("recovery record lacks observed old/new inode bytes: %s", name)
	}
	return nil
}

func agyConcurrencySelectedRecovery(selection AgyLayerSelection, view agyConcurrencySnapshot) error {
	if selection.Recovery == nil || filepath.Dir(selection.Recovery.Path) != filepath.Dir(view.Paths[selection.Layer]) {
		return fmt.Errorf("successful publisher did not select its exact recovery record")
	}
	name := selection.Layer + ":" + filepath.Base(selection.Recovery.Path)
	file, ok := view.Recovery[name]
	if !ok || file.SHA256 != selection.Recovery.SHA256 {
		return fmt.Errorf("successful publisher recovery pin is missing or changed: %s", name)
	}
	return agyConcurrencyRecoveryRecord(name, file, view)
}

func agyConcurrencyActive(view agyConcurrencySnapshot) (int, error) {
	active := 0
	for layer, file := range view.Layers {
		if file.Error != "" || file.Mode != 0640 || !json.Valid(file.Raw) || !bytes.HasPrefix(file.Raw, []byte(agyConcurrencyOld[:len(agyConcurrencyOld)-2])) {
			return 0, fmt.Errorf("%s settings torn, unreadable, mode damaged or unrelated literal bytes changed: %+v", layer, file)
		}
		var fields map[string]json.RawMessage
		if err := json.Unmarshal(file.Raw, &fields); err != nil {
			return 0, err
		}
		if raw, exists := fields["cairn-coordination"]; exists {
			var group map[string][]struct{ Type, Command string }
			if err := json.Unmarshal(raw, &group); err != nil {
				return 0, err
			}
			count := 0
			for _, hooks := range group {
				for _, hook := range hooks {
					if hook.Type != "command" || hook.Command != "/pinned/cairn-first hook" && hook.Command != "/pinned/cairn-second hook" {
						return 0, fmt.Errorf("unexpected dispatch in %s: %+v", layer, hook)
					}
					count++
				}
			}
			if count > 0 {
				active++
			}
		}
	}
	for name, file := range view.Recovery {
		if _, base, ok := strings.Cut(name, ":"); ok && strings.HasPrefix(base, ".cairn-recovery-agy-") {
			if err := agyConcurrencyRecoveryRecord(name, file, view); err != nil {
				return 0, err
			}
			continue
		}
		if file.Error != "" || !json.Valid(file.Raw) || file.Mode != 0640 || !bytes.HasPrefix(file.Raw, []byte(agyConcurrencyOld[:len(agyConcurrencyOld)-2])) {
			return 0, fmt.Errorf("recovery/staged member damaged: %s %+v", name, file)
		}
	}
	return active, nil
}

func agyConcurrencyRunCase(t *testing.T, dir, image, pin string, schedule agyConcurrencySchedule) {
	t.Helper()
	if err := os.Mkdir(dir, 0700); err != nil {
		t.Fatal(err)
	}
	account, workspace := filepath.Join(dir, "account"), filepath.Join(dir, "workspace")
	for _, root := range []string{account, workspace} {
		if err := os.Mkdir(root, 0700); err != nil {
			t.Fatal(err)
		}
	}
	for _, path := range []string{filepath.Join(account, ".gemini/config/hooks.json"), filepath.Join(workspace, ".agents/hooks.json")} {
		agyTestWrite(t, path, agyConcurrencyOld, 0640)
	}
	plans := []agyConcurrencyPlan{{Name: "first", Plan: AgyLayerPlan{AccountHome: account, Workspace: workspace, Layer: "account"}}, {Name: "second", Plan: AgyLayerPlan{AccountHome: account, Workspace: workspace, Layer: "project"}}}
	if schedule.sameLayer {
		plans[1].Plan.Layer = "account"
	}
	record := agyConcurrencyCaseRecord{Name: schedule.name, Prepared: map[string]agyConcurrencyFile{}, Processes: map[string]agyConcurrencyPeerRecord{}, Limits: []string{"Only existing outside-action observer points are controlled.", "No pause inside finalcheck/rename or after the last other-layer recheck.", "No legacy exclusion, zero-race, hard-kill or installed/native qualification."}}
	for i := range plans {
		path := filepath.Join(dir, plans[i].Name+"-settings.json")
		prepared := fmt.Sprintf(`{"cairn-coordination":{"Stop":[{"type":"command","command":"/pinned/cairn-%s hook","timeout":15}]}}`, plans[i].Name)
		agyTestWrite(t, path, prepared, 0400)
		plans[i].Plan.Settings = AgyLayerSettings{Path: path, SHA256: agyConcurrencyHash([]byte(prepared))}
		plans[i].ImageSHA256 = pin
		record.Prepared[plans[i].Name] = agyConcurrencyRead(path)
	}
	record.Plans = plans
	var peers []*agyConcurrencyPeer
	defer func() {
		for _, peer := range peers {
			peer.join(t)
			entry := agyConcurrencyPeerRecord{PID: peer.command.Process.Pid, Exit: peer.exit, Joined: peer.joined, Stdout: peer.output.Bytes(), Events: peer.events, CleanupError: peer.cleanupError, Argv: append([]string(nil), peer.command.Args...)}
			if peer.joined {
				entry.Stderr = append([]byte(nil), peer.diagnostic.Bytes()...)
			}
			record.Processes[peer.name] = entry
			if err := os.WriteFile(filepath.Join(dir, peer.name+".stdout"), entry.Stdout, 0600); err != nil {
				t.Error(err)
			}
			if err := os.WriteFile(filepath.Join(dir, peer.name+".stderr"), entry.Stderr, 0600); err != nil {
				t.Error(err)
			}
		}
		record.Snapshots = append(record.Snapshots, struct {
			Point string
			View  agyConcurrencySnapshot
		}{"cleanup-after-both-join-attempts", agyConcurrencyObserve(plans[0].Plan)})
		agyConcurrencyWrite(t, filepath.Join(dir, "receipt.json"), record)
		t.Logf("finite schedule=%s transient_double_active=%v terminal_double_active=%v both_error=%v evidence=%s", schedule.name, record.TransientDoubleActive, record.TerminalDoubleActive, record.BothError, dir)
	}()
	capture := func(point string) agyConcurrencySnapshot {
		view := agyConcurrencyObserve(plans[0].Plan)
		record.Snapshots = append(record.Snapshots, struct {
			Point string
			View  agyConcurrencySnapshot
		}{point, view})
		active, err := agyConcurrencyActive(view)
		if err != nil {
			record.Counterexample = point + ": " + err.Error()
			t.Fatal(record.Counterexample)
		}
		if active == 2 {
			record.TransientDoubleActive = true
			record.Counterexample = point + ": transient cross-layer both-active"
			t.Fatal(record.Counterexample)
		}
		return view
	}
	initial := capture("before-both-start")
	own := func(peer *agyConcurrencyPeer) { peer.releases = &record.Releases; peers = append(peers, peer) }
	first := agyConcurrencyStart(t, dir, image, pin, plans[0], own)
	second := agyConcurrencyStart(t, dir, image, pin, plans[1], own)
	for _, peer := range peers {
		if peer.current.Initial == nil || !reflect.DeepEqual(initial.Layers, peer.current.Initial.Layers) || !reflect.DeepEqual(initial.Paths, peer.current.Initial.Paths) {
			t.Fatal("competing helpers did not begin with same recorded layer facts", peer.current)
		}
		peer.advance(t, "before:recovery-sync")
		if peer.current.Kind != "boundary" || peer.current.Point != "before:recovery-sync" {
			t.Fatal("helper did not reach controlled publication from original facts", peer.current)
		}
	}
	ready := capture("both-before-recovery-sync")
	if !reflect.DeepEqual(initial.Layers, ready.Layers) {
		t.Fatal("initial layer facts changed before either checked publisher released")
	}
	if schedule.contended {
		first.advance(t, "before:publication")
		second.advance(t, "before:publication")
		capture("both-durable-intents-before-publication")
		first.release(t)
		second.release(t)
		first.receive(t)
		second.receive(t)
		capture("both-publication-releases-observed")
		first.advance(t, "")
		capture("first-terminal-second-held-or-terminal")
		second.advance(t, "")
	} else {
		winner, loser := first, second
		if schedule.secondFirst {
			winner, loser = second, first
		}
		winner.advance(t, schedule.hold)
		capture("winner-" + map[bool]string{true: schedule.hold, false: "terminal"}[schedule.hold != ""])
		loser.advance(t, "")
		capture("loser-terminal")
		winner.advance(t, "")
	}
	first.join(t)
	second.join(t)
	terminal := capture("after-both-joined")
	active, err := agyConcurrencyActive(terminal)
	if err != nil {
		record.Counterexample = err.Error()
		t.Fatal(err)
	}
	record.TerminalDoubleActive = active == 2
	record.BothError = first.current.Error != "" && second.current.Error != ""
	for _, peer := range peers {
		if peer.current.Kind != "result" || peer.exit != map[bool]int{true: 1, false: 0}[peer.current.Error != ""] || !peer.joined || peer.cleanupError != "" {
			t.Fatalf("actual process completion/exit/join differs: %s %+v exit=%d cleanup=%s", peer.name, peer.current, peer.exit, peer.cleanupError)
		}
		if peer.current.Error == "" && peer.current.Selection.Changed {
			if err := agyConcurrencySelectedRecovery(peer.current.Selection, terminal); err != nil {
				t.Fatal(err)
			}
		}
	}
	if active == 2 {
		record.Counterexample = "terminal cross-layer both-active after both checked publishers joined"
		t.Fatal(record.Counterexample)
	}
	if !schedule.contended {
		winner, loser, index := first, second, 0
		if schedule.secondFirst {
			winner, loser, index = second, first, 1
		}
		layer := plans[index].Plan.Layer
		actual := terminal.Layers[layer]
		selected := winner.current.Selection
		wantPath := map[string]string{"account": filepath.Join(account, ".gemini/config/hooks.json"), "project": filepath.Join(workspace, ".agents/hooks.json")}[layer]
		if winner.current.Error != "" || winner.exit != 0 || !selected.Changed || selected.Layer != layer || selected.Path != wantPath || selected.SHA256 != actual.SHA256 || active != 1 || loser.current.Error == "" || loser.exit != 1 {
			record.Counterexample = "ordered schedule did not produce the intended actual successful winner and checked refusal"
			t.Fatal(record.Counterexample, winner.current, loser.current)
		}
		var settings, prepared map[string]json.RawMessage
		if err := json.Unmarshal(actual.Raw, &settings); err != nil {
			t.Fatal(err)
		}
		if err := json.Unmarshal(record.Prepared[plans[index].Name].Raw, &prepared); err != nil {
			t.Fatal(err)
		}
		if !bytes.Equal(settings["cairn-coordination"], prepared["cairn-coordination"]) {
			record.Counterexample = "ordered winner's actual group differs from exact prepared input"
			t.Fatal(record.Counterexample)
		}
	}
}

func TestAgyLayerConcurrencyCheckedPublishers(t *testing.T) {
	base := os.Getenv("CAIRN_R06_AGY_CONCURRENCY_EVIDENCE")
	if base == "" {
		cache, err := os.UserCacheDir()
		if err != nil {
			t.Fatal(err)
		}
		base = filepath.Join(cache, "cairn-r06-agy-concurrency-tests")
	}
	if !canonical(base) {
		t.Fatal("evidence directory must be an explicit canonical owned path")
	}
	if err := os.MkdirAll(base, 0700); err != nil {
		t.Fatal(err)
	}
	info, err := os.Lstat(base)
	if err != nil || !info.IsDir() || info.Mode()&os.ModeSymlink != 0 || info.Mode().Perm()&0022 != 0 {
		t.Fatal("unsafe evidence directory", err)
	}
	stat, ok := info.Sys().(*syscall.Stat_t)
	if !ok || stat.Uid != uint32(os.Geteuid()) {
		t.Fatal("evidence directory owner differs")
	}
	cache, err := os.UserCacheDir()
	if err != nil {
		t.Fatal(err)
	}
	// Runtime inputs must not inherit a group-writable evidence ancestor.
	// Retain a pointer in the requested evidence directory; never chmod it.
	run, err := os.MkdirTemp(cache, "cairn-agy-concurrency-")
	if err != nil {
		t.Fatal(err)
	}
	executable, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(executable)
	if err != nil {
		t.Fatal(err)
	}
	image := filepath.Join(run, "checked-helper-image")
	if err := os.WriteFile(image, raw, 0500); err != nil {
		t.Fatal(err)
	}
	build, err := buildinfo.ReadFile(image)
	if err != nil {
		t.Fatal("actual helper build identity unavailable", err)
	}
	pin := agyConcurrencyHash(raw)
	agyConcurrencyWrite(t, filepath.Join(base, filepath.Base(run)+".json"), struct{ Run, Image, SHA256 string }{run, image, pin})
	agyConcurrencyWrite(t, filepath.Join(run, "before-use-image.json"), struct {
		Path, SHA256 string
		Bytes        int
		Build        any
		Schedules    []agyConcurrencySchedule
	}{image, pin, len(raw), build, agyConcurrencySchedules})
	t.Logf("actual helper before-use path=%s sha256=%s bytes=%d Go=%s", image, pin, len(raw), build.GoVersion)
	for _, schedule := range agyConcurrencySchedules {
		if !t.Run(schedule.name, func(t *testing.T) { agyConcurrencyRunCase(t, filepath.Join(run, schedule.name), image, pin, schedule) }) {
			break
		}
	}
}

func TestAgyLayerConcurrencyRecoverySnapshotValidation(t *testing.T) {
	for _, layer := range []string{"account", "project"} {
		t.Run(layer, func(t *testing.T) {
			plan, selected, other := agyTestPlan(t, layer, true)
			for _, path := range []string{selected, other} {
				agyTestWrite(t, path, agyConcurrencyOld, 0640)
			}
			prepared := `{"cairn-coordination":{"Stop":[{"type":"command","command":"/pinned/cairn-first hook","timeout":15}]}}`
			agyTestWrite(t, plan.Settings.Path, prepared, 0400)
			plan.Settings.SHA256 = agyConcurrencyHash([]byte(prepared))
			selection, err := PublishAgyLayer(context.Background(), plan)
			if err != nil || !selection.Changed || selection.Recovery == nil {
				t.Fatal("actual publisher did not retain recovery correspondence", selection, err)
			}
			view := agyConcurrencyObserve(plan)
			if active, err := agyConcurrencyActive(view); err != nil || active != 1 {
				t.Fatal("actual retained snapshot invalid", active, err)
			}
			if err := agyConcurrencySelectedRecovery(selection, view); err != nil {
				t.Fatal(err)
			}
			name := layer + ":" + filepath.Base(selection.Recovery.Path)
			original := view.Recovery[name]
			clone := func() agyConcurrencySnapshot {
				raw, err := json.Marshal(view)
				if err != nil {
					t.Fatal(err)
				}
				var copy agyConcurrencySnapshot
				if err := json.Unmarshal(raw, &copy); err != nil {
					t.Fatal(err)
				}
				return copy
			}
			change := func(view *agyConcurrencySnapshot, mutate func(*agyRecoveryRecord)) {
				file := view.Recovery[name]
				var record agyRecoveryRecord
				if err := json.Unmarshal(file.Raw, &record); err != nil {
					t.Fatal(err)
				}
				mutate(&record)
				raw, err := json.Marshal(record)
				if err != nil {
					t.Fatal(err)
				}
				file.Raw, file.SHA256 = raw, agyConcurrencyHash(raw)
				view.Recovery[name] = file
			}
			for _, test := range []struct {
				name   string
				mutate func(*agyConcurrencySnapshot)
			}{
				{"mode", func(v *agyConcurrencySnapshot) { f := v.Recovery[name]; f.Mode = 0640; v.Recovery[name] = f }},
				{"owner", func(v *agyConcurrencySnapshot) { f := v.Recovery[name]; f.UID++; v.Recovery[name] = f }},
				{"alias", func(v *agyConcurrencySnapshot) { f := v.Recovery[name]; f.Links = 2; v.Recovery[name] = f }},
				{"schema", func(v *agyConcurrencySnapshot) { change(v, func(r *agyRecoveryRecord) { r.Schema = "unknown" }) }},
				{"layer", func(v *agyConcurrencySnapshot) { change(v, func(r *agyRecoveryRecord) { r.Layer = "unknown" }) }},
				{"root", func(v *agyConcurrencySnapshot) {
					change(v, func(r *agyRecoveryRecord) { r.Selected.Root = "/outside" })
				}},
				{"temporary", func(v *agyConcurrencySnapshot) { change(v, func(r *agyRecoveryRecord) { r.Temporary += "other" }) }},
				{"new-inode", func(v *agyConcurrencySnapshot) { change(v, func(r *agyRecoveryRecord) { r.NewFile.Ino++ }) }},
				{"new-bytes", func(v *agyConcurrencySnapshot) {
					change(v, func(r *agyRecoveryRecord) { r.NewSHA256 = strings.Repeat("0", 64) })
				}},
				{"noncanonical", func(v *agyConcurrencySnapshot) {
					f := v.Recovery[name]
					f.Raw = append(f.Raw, '\n')
					v.Recovery[name] = f
				}},
				{"missing-old-inode", func(v *agyConcurrencySnapshot) {
					var record agyRecoveryRecord
					if err := json.Unmarshal(v.Recovery[name].Raw, &record); err != nil {
						t.Fatal(err)
					}
					delete(v.Recovery, layer+":"+record.Temporary)
				}},
			} {
				t.Run(test.name, func(t *testing.T) {
					mutated := clone()
					test.mutate(&mutated)
					if _, err := agyConcurrencyActive(mutated); err == nil {
						t.Fatal("snapshot accepted changed recovery evidence")
					}
				})
			}
			t.Run("missing-success-record", func(t *testing.T) {
				mutated := clone()
				delete(mutated.Recovery, name)
				if err := agyConcurrencySelectedRecovery(selection, mutated); err == nil {
					t.Fatal("successful publisher lost its selected record")
				}
			})
			if actual := agyConcurrencyRead(selection.Recovery.Path); !reflect.DeepEqual(original, actual) {
				t.Fatal("in-memory negative cases changed actual retained evidence")
			}
		})
	}
}
