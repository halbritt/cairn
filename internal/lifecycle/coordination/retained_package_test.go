package coordination

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"testing"

	"github.com/halbritt/cairn/internal/lifecycle/install"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// Producer and independent Go readers consume the same authenticated packages.
// These are disposable three-host shapes, never native/application qualification.
func TestRetainedPackageProducerReaderParity(t *testing.T) {
	_, source, _, _ := runtime.Caller(0)
	repo := filepath.Clean(filepath.Join(filepath.Dir(source), "../../.."))
	stager := filepath.Join(t.TempDir(), "stager")
	build := exec.Command("go", "build", "-buildvcs=false", "-o", stager, "./cmd/cairn-stage-lifecycle")
	build.Dir = repo
	if raw, err := build.CombinedOutput(); err != nil {
		t.Fatalf("build fixture dependency: %v: %s", err, raw)
	}
	if err := os.Chmod(stager, 0500); err != nil {
		t.Fatal(err)
	}
	root, err := os.MkdirTemp("/dev/shm", "cairn-retained-package-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if err := os.RemoveAll(root); err != nil {
			t.Error(err)
		}
	})
	produce := exec.Command("python3", "-B", filepath.Join(repo, "scripts/test_retained_lifecycle_package.py"), "--fixtures", root, "--stager", stager)
	if selected := os.Getenv("CAIRN_RETAINED_PACKAGE_SOURCE_FIXTURE"); selected != "" {
		produce.Args = append(produce.Args, "--source-sets", selected)
	}
	if selected := os.Getenv("CAIRN_RETAINED_PACKAGE_SIDECAR_FIXTURE"); selected != "" {
		produce.Args = append(produce.Args, "--source-sidecars", selected)
	}
	var producerErrors bytes.Buffer
	produce.Stderr = &producerErrors
	raw, err := produce.Output()
	if err != nil {
		t.Fatalf("produce packages: %v: %s %s", err, raw, producerErrors.Bytes())
	}
	var cases []struct {
		Name, Root                 string
		Expected, Producer, Python bool
		Selection                  WatchCensusPin
		Members                    []struct {
			Name                  string
			Config, Binding, Argv WatchCensusPin
			Proof                 json.RawMessage
		}
	}
	if err := json.Unmarshal(raw, &cases); err != nil {
		t.Fatal(err)
	}
	expectedCases := 42
	if os.Getenv("CAIRN_RETAINED_PACKAGE_SIDECAR_FIXTURE") != "" {
		expectedCases++
	}
	if len(cases) != expectedCases {
		t.Fatalf("missing parity cases: %d", len(cases))
	}
	for _, tc := range cases {
		t.Run(tc.Name, func(t *testing.T) {
			if tc.Producer != tc.Expected || tc.Python != tc.Expected {
				t.Fatal("producer/Python parity differs")
			}
			read := func(pin WatchCensusPin) ([]byte, error) {
				raw, digest, err := compatibilityOpened(pin.Path, 1048576, uint32(os.Getuid()))
				if err == nil && digest != pin.SHA256 {
					return nil, retainedPackageError()
				}
				return raw, err
			}
			profileRead := func(pin WatchCensusPin) ([]byte, error) {
				observed, err := bootstrapLegacyProfile(pin.Path)
				if err == nil && observed.SHA256 != pin.SHA256 {
					return nil, retainedPackageError()
				}
				return observed.Raw, err
			}
			pkg, err := readRetainedPackage(context.Background(), tc.Selection, read, profileRead)
			if (err == nil) != tc.Expected {
				t.Fatalf("shared Go validator: %v; expected acceptance %t", err, tc.Expected)
			}
			if tc.Expected {
				ctx, cancel := context.WithCancel(context.Background())
				cancel()
				calls := 0
				forbiddenRead := func(WatchCensusPin) ([]byte, error) {
					calls++
					return nil, errors.New("canceled validation performed a content read")
				}
				if _, err := readRetainedPackage(ctx, tc.Selection, forbiddenRead, forbiddenRead); !errors.Is(err, context.Canceled) || calls != 0 {
					t.Errorf("canceled observation: reads=%d error=%v", calls, err)
				}
			}
			files, err := install.ReadGeneration(context.Background(), install.Generation{Path: tc.Selection.Path, SHA256: tc.Selection.SHA256})
			if err != nil {
				t.Fatal(err)
			}
			members := map[string]WatchCensusPin{}
			for _, file := range files {
				members[file.Name] = WatchCensusPin{Path: file.Path, SHA256: file.SHA256}
			}
			for _, member := range tc.Members {
				profile := stage2ServicesProfile()
				b := &bootstrapRead{
					plan: &UnitBootstrapPlan{Schema: "cairn.unit-bootstrap/2", Root: tc.Root}, paths: &watchCensusRead{inodes: map[string]string{}},
					drain: &watchDrainRead{cli: &CLI{Profile: profile}, facts: map[string]string{}}, credentials: map[string]os.FileInfo{}, binaryDirs: map[string]bool{},
				}
				selected := BootstrapRetainedAssociation{Binding: member.Binding, Argv: member.Argv, Package: &tc.Selection}
				if err := b.retainedPackage(selected, member.Config.Path); (err == nil) != tc.Expected {
					t.Errorf("bootstrap member %s: %v; expected acceptance %t", member.Name, err, tc.Expected)
				}
				configRaw, err := os.ReadFile(member.Config.Path)
				if err != nil {
					t.Fatal(err)
				}
				config, err := value.DecodeBytes(configRaw, 0)
				if err != nil {
					t.Fatal(err)
				}
				b.plan.Associations = map[string]BootstrapRetainedAssociation{member.Name: selected}
				if _, _, err := b.association(member.Name, config); (err == nil) != tc.Expected {
					t.Errorf("full bootstrap association %s: %v; expected acceptance %t", member.Name, err, tc.Expected)
				}
				facts := map[string]string{}
				r := &watchCensusRead{
					catalog: WatchCensusCatalog{Root: tc.Root}, local: &watchDrainRead{cli: &CLI{Profile: profile}, tokenPaths: map[string]bool{}, facts: facts},
					report: WatchUnitCensus{Facts: facts}, files: map[string]string{}, inodes: map[string]string{}, images: map[string]bool{},
				}
				producerRaw, err := json.Marshal(map[string]any{"lifecycle_selection": map[string]string{"path": tc.Selection.Path, "sha256": tc.Selection.SHA256}, "retained_python": member.Proof})
				if err != nil {
					t.Fatal(err)
				}
				producer, err := value.DecodeBytesWithObjectPolicy(producerRaw, 0, value.DuplicateReject)
				if err != nil {
					t.Fatal(err)
				}
				node := &WatchCensusMember{Config: member.Config, Binding: member.Binding, Mode: "cairn.inbox-recall-binding/2"}
				if err := r.claudeRetainedMembers(node, members, producer); (err == nil) != tc.Expected {
					t.Errorf("native reader member %s: %v; expected acceptance %t", member.Name, err, tc.Expected)
				}
				if tc.Expected {
					originalRoot := b.plan.Root
					b.plan.Root = filepath.Join(tc.Root, "unrelated-publication-root")
					if err := b.retainedPackage(selected, member.Config.Path); err == nil {
						t.Error("bootstrap accepted an external outer generation")
					}
					b.plan.Root = originalRoot
					r.nodes = map[string]*WatchCensusMember{member.Config.Path: node}
					launches, err := r.claudeHookLaunches(WatchCensusClaudeHooks{Config: member.Config}, producer)
					if err != nil || !nativeEqual(launches, pkg.Object.Get("members").Get(member.Name).Get("argv")) {
						t.Errorf("native launch selection %s: %v", member.Name, err)
					}
					unselectedRoot := filepath.Join(tc.Root, "unselected-"+member.Name)
					if err := os.Mkdir(unselectedRoot, 0700); err != nil {
						t.Fatal(err)
					}
					metadata := pkg.Files["retained-package.json"]
					unselected, err := install.StageGeneration(context.Background(), unselectedRoot, "11111111-1111-4111-8111-111111111111", []install.File{{Name: "unselected-package.json", Path: metadata.Path, SHA256: metadata.SHA256}})
					if err != nil {
						t.Fatal(err)
					}
					for _, mutation := range []string{"none", "lifecycle-pin", "package-pin", "package-path", "member", "config", "selected-config", "binding", "nonmember-generation"} {
						t.Run(member.Name+"/standalone/"+mutation, func(t *testing.T) {
							candidate, err := value.DecodeBytesWithObjectPolicy(producerRaw, 0, value.DuplicateReject)
							if err != nil {
								t.Fatal(err)
							}
							copyNode := *node
							selectedConfig := member.Config
							switch mutation {
							case "lifecycle-pin":
								candidate.Get("lifecycle_selection").Set("sha256", value.Text(string(bytes.Repeat([]byte{'0'}, 64))))
							case "package-pin":
								candidate.Get("retained_python").Get("package").Set("sha256", value.Text(string(bytes.Repeat([]byte{'0'}, 64))))
							case "package-path":
								candidate.Get("retained_python").Get("package").Set("path", value.Text(tc.Selection.Path+".other"))
							case "member":
								candidate.Get("retained_python").Set("member", value.Text("foreign-member"))
							case "config":
								copyNode.Config.SHA256 = string(bytes.Repeat([]byte{'0'}, 64))
							case "selected-config":
								selectedConfig.SHA256 = string(bytes.Repeat([]byte{'0'}, 64))
							case "binding":
								copyNode.Binding.SHA256 = string(bytes.Repeat([]byte{'0'}, 64))
							case "nonmember-generation":
								pin := &value.Value{Kind: value.Object}
								pin.Set("path", value.Text(unselected.Path))
								pin.Set("sha256", value.Text(unselected.SHA256))
								candidate.Set("lifecycle_selection", pin)
								candidate.Get("retained_python").Set("package", pin)
							}
							facts := map[string]string{}
							standalone := &watchCensusRead{catalog: r.catalog, nodes: map[string]*WatchCensusMember{member.Config.Path: &copyNode},
								local:  &watchDrainRead{cli: r.local.cli, tokenPaths: map[string]bool{}, facts: facts},
								report: WatchUnitCensus{Facts: facts}, files: map[string]string{}, inodes: map[string]string{}, images: map[string]bool{}}
							got, err := standalone.claudeHookLaunches(WatchCensusClaudeHooks{Config: selectedConfig}, candidate)
							if (err == nil) != (mutation == "none") {
								t.Fatalf("standalone selection %s: %v", mutation, err)
							}
							if err == nil && (!nativeEqual(got, pkg.Object.Get("members").Get(member.Name).Get("argv")) || standalone.files[pkg.Files["retained-package.json"].Path] == "") {
								t.Fatal("standalone selection skipped authenticated package membership")
							}
						})
					}
					producer.Get("retained_python").Set("member", value.Text("foreign-member"))
					if err := r.claudeRetainedMembers(node, members, producer); err == nil {
						t.Error("native reader accepted a foreign member proof")
					}
				}
			}
		})
	}
}

func TestRetainedPackageBootstrapSelectorFields(t *testing.T) {
	for _, extra := range []string{"package", "receipt", "receipt-and-package", "unknown"} {
		t.Run(extra, func(t *testing.T) {
			pin := map[string]string{"Path": "/fixture/manifest.json", "SHA256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
			association := map[string]any{"binding": pin, "argv": pin}
			if extra == "receipt-and-package" {
				association["receipt"], association["package"] = pin, pin
			} else {
				association[extra] = pin
			}
			raw, err := json.Marshal(map[string]any{"schema": "cairn.unit-bootstrap/2", "associations": map[string]any{"member": association}})
			if err != nil {
				t.Fatal(err)
			}
			tree, err := retainedPackageJSON(raw)
			if err != nil {
				t.Fatal(err)
			}
			if err := bootstrapExtensionKeys(tree); (err == nil) != (extra == "package" || extra == "receipt") {
				t.Fatalf("selector %s: %v", extra, err)
			}
		})
	}
}
