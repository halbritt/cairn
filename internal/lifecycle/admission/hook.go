package admission

// HookInvocation carries the checked parent observation unchanged across the
// private activation boundary. Digests are consistency checks, not authority.
type HookInvocation struct {
	EntryPoint, ConfigPath, ReceiptPath, ReceiptSHA256 string
	ConfigSHA256, BindingSHA256                        string
	Event                                              []byte
	Environment                                        []string
	Directory                                          string
}
