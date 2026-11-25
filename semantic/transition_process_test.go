package semantic

import (
	"fmt"
	"os"
	"strconv"
	"strings"
)

// transitionProcessStatus reads one child's parent PID and state for the
// model-free S-05 process-ownership checks.
func transitionProcessStatus(pid int) (int, string, error) {
	data, err := os.ReadFile(fmt.Sprintf("/proc/%d/status", pid))
	if err != nil {
		return 0, "", err
	}
	parent, state := -1, ""
	for _, line := range strings.Split(string(data), "\n") {
		fields := strings.Fields(line)
		if len(fields) >= 2 && fields[0] == "PPid:" {
			parent, err = strconv.Atoi(fields[1])
		}
		if len(fields) >= 2 && fields[0] == "State:" {
			state = fields[1]
		}
	}
	if err != nil || parent < 0 || state == "" {
		return 0, "", fmt.Errorf("unreadable process ownership/state for %d", pid)
	}
	return parent, state, nil
}
