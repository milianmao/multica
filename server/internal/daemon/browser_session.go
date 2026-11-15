package daemon

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"log/slog"
	"os"
	"regexp"
	"sort"
	"sync"
	"time"
)

// QA built-in isolated browser: the session registry.
//
// The daemon owns the session — its state, the takeover lock, the idle and
// hard TTLs, and the two-phase cleanup that removes the partition directory.
// Desktop only carries the visible WebContentsView; it never decides when a
// session ends (SDD §2, §3.4).
//
// The registry is purely in memory: a daemon restart closes every session by
// definition, and there is no database change for this feature (SDD §2, §10).

// browserSessionState mirrors the SDD §2 state diagram.
type browserSessionState string

const (
	// browserStateRequested exists only while the task has asked and no
	// Desktop host has attached yet.
	browserStateRequested browserSessionState = "requested"
	browserStateHosted    browserSessionState = "hosted"
	// browserStateOrphaned means the host stopped heartbeating; the view may
	// still be alive, so it is not the same as closing.
	browserStateOrphaned browserSessionState = "orphaned"
	// browserStateClosing is the only pre-terminal state that deletes a
	// directory, and only at the end of the second phase.
	browserStateClosing browserSessionState = "closing"
	browserStateClosed  browserSessionState = "closed"
	// browserStateFailed is reserved for "never hosted" failures such as a
	// missing Desktop (BROWSER_HOST_OFFLINE). A session that was hosted once
	// never returns here.
	browserStateFailed browserSessionState = "failed"
)

// browserLockOwner is orthogonal to state: taking over changes only this
// field, so an agent↔human handoff never produces a state transition.
type browserLockOwner string

const (
	browserLockNone  browserLockOwner = "none"
	browserLockAgent browserLockOwner = "agent"
	browserLockHuman browserLockOwner = "human"
)

const (
	browserCapabilityFull = "full"
	browserCapabilityNone = "none"
)

// Default timers from SDD §3.4. They are fields on the registry so focused
// tests can shrink them without a build tag.
const (
	defaultBrowserIdleTTL        = 15 * time.Minute
	defaultBrowserHumanHardLimit = 30 * time.Minute
	// The desktop polls for at most 4s per heartbeat and ticks every 2s, so an
	// alive host produces an arrival at least every ~6s. 30s is five missed
	// arrivals: enough that a slow GC pause or a laptop resume does not kill a
	// live session, short enough that a crashed Desktop is noticed promptly.
	defaultBrowserHeartbeatTimeout = 30 * time.Second
	defaultBrowserSweepInterval    = time.Second

	defaultBrowserCleanupMaxTries = 3
	// defaultBrowserLockWait is the per-tool-call lock budget, shared with
	// browser_wait (SDD §8.1): one context covers both so a takeover can never
	// turn one tool call into 60 s of silence.
	// defaultBrowserClosingAckTimeout is two heartbeat windows: long enough for
	// an ack already in flight to land, short enough that a crashed host's
	// session does not outlive the crash by more than a minute.
	defaultBrowserClosingAckTimeout = 2 * defaultBrowserHeartbeatTimeout
	// defaultBrowserLockWait is the per-tool-call lock budget, shared with
	// browser_wait (SDD §8.1): one context covers both so a takeover can never
	// turn one tool call into 60 s of silence.
	defaultBrowserLockWait = 30 * time.Second
)

// browserEvidence is the last screenshot the host delivered. Only the
// attachment id is exposed; there is no local path (SDD §3.3).
type browserEvidence struct {
	AttachmentID string    `json:"attachment_id"`
	CapturedAt   time.Time `json:"captured_at"`
	ViewportOnly bool      `json:"viewport_only"`
}

// browserSessionURL carries host+path only. Query strings may hold one-time
// tokens, so they never enter the session view.
type browserSessionURL struct {
	Host string `json:"host"`
	Path string `json:"path"`
}

// browserCommand is one action the daemon asks the Desktop host to execute in
// its WebContentsView. The daemon cannot drive the view itself: a
// process-level debug port is forbidden (invariant I1) and
// webContents.debugger is main-process-only.
type browserCommand struct {
	ID     string         `json:"id"`
	Action string         `json:"action"`
	Params map[string]any `json:"params,omitempty"`
}

// browserCommandResult is the host's answer to one browserCommand.
type browserCommandResult struct {
	ID     string         `json:"id"`
	OK     bool           `json:"ok"`
	Error  string         `json:"error,omitempty"`
	Text   string         `json:"text,omitempty"`
	Result map[string]any `json:"result,omitempty"`
}

type browserSession struct {
	ID          string
	TaskID      string
	IssueID     string
	WorkspaceID string
	AgentID     string
	Partition   string
	Capability  string

	State     browserSessionState
	Lock      browserLockOwner
	URL       browserSessionURL
	StartedAt time.Time
	// IdleExpiresAt is recomputed on every tool call and heartbeat activity.
	IdleExpiresAt time.Time
	// HardDeadline counts from the takeover, and is never paused (D4).
	HardDeadline time.Time
	LastEvidence *browserEvidence
	ErrorCode    string
	ErrorMessage string

	// Host-plane facts reported by Desktop.
	HostWindowID  string
	cdpReady      bool
	heartbeatSeq  int64
	lastHeartbeat time.Time
	closedAcked   bool
	// closingSince is when the session entered `closing`. The no-ack timeout is
	// measured from it, so a host that never answers cannot pin the entry.
	closingSince time.Time

	// Command channel: the host long-polls for work and reports results.
	commands      []browserCommand
	commandSignal chan struct{}
	pending       map[string]chan browserCommandResult

	// change broadcasts every state or lock change to lock waiters.
	change chan struct{}
}

func (s *browserSession) notifier() chan struct{} {
	if s.commandSignal == nil {
		s.commandSignal = make(chan struct{}, 1)
	}
	return s.commandSignal
}

func (s *browserSession) notifyCommand() {
	select {
	case s.notifier() <- struct{}{}:
	default:
	}
}

func (s *browserSession) notifyChange() {
	if s.change == nil {
		s.change = make(chan struct{})
		return
	}
	close(s.change)
	s.change = make(chan struct{})
}

func (s *browserSession) changeSignal() chan struct{} {
	if s.change == nil {
		s.change = make(chan struct{})
	}
	return s.change
}

// browserSessionRegistry holds every live session of this daemon.
type browserSessionRegistry struct {
	mu      sync.Mutex
	byID    map[string]*browserSession
	byTask  map[string]string
	now     func() time.Time
	idleTTL time.Duration
	// humanHardLimit bounds a takeover; expiry forces closing regardless of
	// the idle timer's human-lock pause.
	humanHardLimit   time.Duration
	heartbeatTimeout time.Duration
	// closingAckTimeout bounds how long `closing` waits for the host's phase-two
	// ack before the daemon finalizes the session on its own.
	closingAckTimeout time.Duration
	logger            *slog.Logger
}

func newBrowserSessionRegistry(logger *slog.Logger) *browserSessionRegistry {
	if logger == nil {
		logger = slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelError}))
	}
	return &browserSessionRegistry{
		byID:              make(map[string]*browserSession),
		byTask:            make(map[string]string),
		now:               time.Now,
		idleTTL:           defaultBrowserIdleTTL,
		humanHardLimit:    defaultBrowserHumanHardLimit,
		heartbeatTimeout:  defaultBrowserHeartbeatTimeout,
		closingAckTimeout: defaultBrowserClosingAckTimeout,
		logger:            logger,
	}
}

var (
	errBrowserSessionClosed  = errors.New("browser session is closed")
	errBrowserSessionHosted  = errors.New("browser session is already hosted")
	errBrowserUnknownSession = errors.New("unknown browser session")
)

func newBrowserSessionID() (string, error) {
	value := make([]byte, 8)
	if _, err := rand.Read(value); err != nil {
		return "", err
	}
	return "bs_" + hex.EncodeToString(value), nil
}

// requestBrowserSession returns the task's live session, creating it when none
// exists. Repeated calls are idempotent and return the same session id; a
// closed session is replaced, which is the only way to start over after a
// heartbeat loss (SDD §9 case 2).
func (r *browserSessionRegistry) requestBrowserSession(task activeRepoCheckoutTask, capability string) (*browserSession, error) {
	// The registry is the last gate before an id becomes session state, so the
	// check lives here rather than at one entry point: both planes, and any
	// future caller, get it.
	if err := validateBrowserTaskID(task.TaskID); err != nil {
		return nil, err
	}
	r.mu.Lock()
	defer r.mu.Unlock()

	if id, ok := r.byTask[task.TaskID]; ok {
		if existing, found := r.byID[id]; found && reusableBrowserState(existing.State) {
			r.touchLocked(existing)
			return existing, nil
		}
	}
	id, err := newBrowserSessionID()
	if err != nil {
		return nil, err
	}
	now := r.now()
	session := &browserSession{
		ID:            id,
		TaskID:        task.TaskID,
		IssueID:       task.IssueID,
		WorkspaceID:   task.WorkspaceID,
		AgentID:       task.AgentID,
		Partition:     browserPartitionName(task.TaskID),
		Capability:    capability,
		State:         browserStateRequested,
		Lock:          browserLockNone,
		StartedAt:     now,
		IdleExpiresAt: now.Add(r.idleTTL),
		pending:       make(map[string]chan browserCommandResult),
	}
	r.byID[id] = session
	r.byTask[task.TaskID] = id
	return session, nil
}

// list returns every non-closed session, newest first. Desktop polls it to
// discover sessions it has not attached a view to yet.
func (r *browserSessionRegistry) list() []*browserSession {
	r.mu.Lock()
	defer r.mu.Unlock()
	out := make([]*browserSession, 0, len(r.byID))
	for _, s := range r.byID {
		// `closing` is excluded as well as `closed`: its view is already being
		// torn down, so advertising it invites a register that would revive it.
		if !reusableBrowserState(s.State) {
			continue
		}
		out = append(out, s)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].StartedAt.After(out[j].StartedAt) })
	return out
}

// byTaskID returns the task's session even when closed, so callers can tell
// "never opened" (nil) from "ended" (a closed session).
// touchHost refreshes liveness after an in-flight request proves the host is
// still there. It deliberately does not accept a sequence number: this is not a
// new observation, it is the arrival of an old one finally completing.
func (r *browserSessionRegistry) touchHost(sessionID string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	s, ok := r.byID[sessionID]
	if !ok || s.State == browserStateClosed || s.State == browserStateFailed {
		return
	}
	s.lastHeartbeat = r.now()
}

func (r *browserSessionRegistry) byTaskID(taskID string) *browserSession {
	r.mu.Lock()
	defer r.mu.Unlock()
	id, ok := r.byTask[taskID]
	if !ok {
		return nil
	}
	return r.byID[id]
}

func (r *browserSessionRegistry) bySessionID(sessionID string) *browserSession {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.byID[sessionID]
}

// touchLocked marks activity. A human holding the lock pauses the idle timer
// (D4); the hard deadline still runs.
func (r *browserSessionRegistry) touchLocked(s *browserSession) {
	now := r.now()
	if s.Lock != browserLockHuman {
		s.IdleExpiresAt = now.Add(r.idleTTL)
	}
}

// registerBrowserHost attaches a Desktop host to a requested session.
func (r *browserSessionRegistry) registerBrowserHost(sessionID, windowID string, cdpReady bool) (*browserSession, error) {
	r.mu.Lock()
	s, ok := r.byID[sessionID]
	if !ok {
		r.mu.Unlock()
		return nil, errBrowserUnknownSession
	}
	switch s.State {
	case browserStateClosed, browserStateClosing:
		// A closing session's phase-two instruction has already gone out; a
		// register that reset it to `hosted` would resurrect a session the
		// daemon has decided to end.
		r.mu.Unlock()
		return nil, errBrowserSessionClosed
	}
	s.HostWindowID = windowID
	s.cdpReady = cdpReady
	s.State = browserStateHosted
	s.lastHeartbeat = r.now()
	s.heartbeatSeq = 0
	s.notifyChange()
	snapshot := s
	r.mu.Unlock()
	return snapshot, nil
}

// heartbeat records liveness. seq must strictly increase; a stale or replayed
// value is dropped without changing state (SDD §3.2).
func (r *browserSessionRegistry) heartbeat(sessionID string, seq int64, viewAlive bool, url *browserSessionURL) (*browserSession, bool) {
	r.mu.Lock()
	defer r.mu.Unlock()
	s, ok := r.byID[sessionID]
	if !ok || s.State == browserStateClosed || s.State == browserStateFailed {
		return nil, false
	}
	if seq <= s.heartbeatSeq {
		return nil, false
	}
	s.heartbeatSeq = seq
	s.lastHeartbeat = r.now()
	if !viewAlive && s.State == browserStateHosted {
		// The view died (render-process-gone). The session stays hosted so
		// the next tool call can report BROWSER_CAPABILITY_UNSUPPORTED, but it
		// no longer counts as a live host: mark orphaned so nothing waits on
		// it forever.
		s.State = browserStateOrphaned
		s.notifyChange()
	}
	if url != nil {
		// The registry is the only writer of URL, so the query/fragment cut
		// happens here rather than at each caller: a path with a one-time token
		// must never reach a session view, whatever transport reported it.
		s.URL = browserSessionURL{
			Host: sanitizeBrowserURLPart(url.Host),
			Path: sanitizeBrowserURLPart(url.Path),
		}
	}
	if s.State == browserStateOrphaned && viewAlive {
		// A recovering heartbeat must not resurrect an orphaned session
		// (SDD §9 case 2): a new session is the only way back.
		return s, false
	}
	r.touchLocked(s)
	return s, true
}

var errBrowserLockHuman = errors.New("browser session is taken over by a person")

// takeover flips the lock to human and starts the hard deadline. It is
// callable only from the host plane (host secret), so an agent token can never
// release a person's lock.
func (r *browserSessionRegistry) takeover(sessionID string) (*browserSession, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	s, ok := r.byID[sessionID]
	if !ok {
		return nil, errBrowserUnknownSession
	}
	if s.State != browserStateHosted && s.State != browserStateOrphaned {
		return nil, errBrowserSessionClosed
	}
	if s.Lock == browserLockHuman {
		return s, nil
	}
	s.Lock = browserLockHuman
	s.HardDeadline = r.now().Add(r.humanHardLimit)
	s.notifyChange()
	return s, nil
}

func (r *browserSessionRegistry) release(sessionID string) (*browserSession, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	s, ok := r.byID[sessionID]
	if !ok {
		return nil, errBrowserUnknownSession
	}
	if s.State != browserStateHosted && s.State != browserStateOrphaned {
		return nil, errBrowserSessionClosed
	}
	s.Lock = browserLockAgent
	s.HardDeadline = time.Time{}
	s.IdleExpiresAt = r.now().Add(r.idleTTL)
	s.notifyChange()
	return s, nil
}

// closeBrowserSession moves a session to closing. Repeating it on an already
// closed session is a success, not an error (invariant I6).
func (r *browserSessionRegistry) closeBrowserSession(sessionID string) (*browserSession, bool) {
	r.mu.Lock()
	defer r.mu.Unlock()
	s, ok := r.byID[sessionID]
	if !ok {
		return nil, false
	}
	switch s.State {
	case browserStateClosed:
		return s, true
	case browserStateRequested, browserStateFailed:
		// Nothing was ever hosted: there is no view and no directory.
		r.finalizeLocked(s)
		return s, true
	default:
		s.State = browserStateClosing
		s.closingSince = r.now()
		s.notifyChange()
		return s, true
	}
}

// fail terminates a session that was never hosted, e.g. a request answered with
// BROWSER_HOST_OFFLINE. The entry leaves the registry immediately: D3 requires a
// hard error with nothing queued, so the next request starts clean.
func (r *browserSessionRegistry) fail(sessionID, code, message string) *browserSession {
	r.mu.Lock()
	defer r.mu.Unlock()
	s, ok := r.byID[sessionID]
	if !ok {
		return nil
	}
	s.State = browserStateFailed
	s.ErrorCode = code
	s.ErrorMessage = message
	snapshot := s
	r.closeWaitersLocked(s)
	delete(r.byID, s.ID)
	if r.byTask[s.TaskID] == s.ID {
		delete(r.byTask, s.TaskID)
	}
	return snapshot
}

// ackClosed is the second cleanup phase: the host has destroyed the view, so
// the partition directory can be removed without fighting a live renderer.
func (r *browserSessionRegistry) ackClosed(sessionID string) (*browserSession, bool) {
	r.mu.Lock()
	defer r.mu.Unlock()
	s, ok := r.byID[sessionID]
	if !ok {
		return nil, false
	}
	s.closedAcked = true
	if s.State == browserStateClosed {
		return s, true
	}
	if s.State != browserStateClosing && s.State != browserStateOrphaned && s.State != browserStateHosted {
		return s, false
	}
	if s.closingSince.IsZero() {
		s.closingSince = r.now()
	}
	s.State = browserStateClosing
	r.cleanupLocked(s)
	return s, true
}

// finalizeLocked closes a session that never had a directory.
func (r *browserSessionRegistry) finalizeLocked(s *browserSession) {
	s.State = browserStateClosed
	r.closeWaitersLocked(s)
	s.notifyChange()
	delete(r.byID, s.ID)
	if r.byTask[s.TaskID] == s.ID {
		delete(r.byTask, s.TaskID)
	}
}

// cleanupLocked finalizes a session. It deletes nothing on disk.
//
// The QA view's partition is non-persistent (`qa-task-<id>` has no `persist:`
// prefix), so Chromium keeps its state in the process and writes no profile
// directory. Deleting a path the daemon never created bought no isolation and
// turned a caller-supplied identifier into a filesystem operation; removing
// that path entirely is the fix, not tightening its validation. Desktop's
// `host.destroy()` already releases the view and any in-memory session state.
func (r *browserSessionRegistry) cleanupLocked(s *browserSession) {
	r.finalizeLocked(s)
}

func (r *browserSessionRegistry) closeWaitersLocked(s *browserSession) {
	for id, ch := range s.pending {
		select {
		case ch <- browserCommandResult{ID: id, OK: false, Error: "browser session is closed"}:
		default:
		}
		delete(s.pending, id)
	}
	s.commands = nil
	s.notifyCommand()
}

// sweep applies the three timers. It is the only writer of time-driven
// transitions.
func (r *browserSessionRegistry) sweep() {
	r.mu.Lock()
	defer r.mu.Unlock()
	now := r.now()
	for _, s := range r.byID {
		switch s.State {
		case browserStateClosed:
			continue
		case browserStateClosing:
			// Phase one normally ends with the host's ack. A host that crashed,
			// was closed by the user, or simply never answered must not keep the
			// entry alive forever — that was unbounded memory growth and a
			// half-dead session the next run could inherit. After the no-ack
			// budget the daemon finishes the transition itself.
			if !s.closedAcked && !s.closingSince.IsZero() && now.Sub(s.closingSince) > r.closingAckTimeout {
				r.logger.Warn("browser session closed without a host ack; finalizing",
					"session_id", s.ID, "task_id", s.TaskID)
				r.finalizeLocked(s)
			}
			continue
		}
		// Human hard limit: independent of the idle pause (D4).
		if s.Lock == browserLockHuman && !s.HardDeadline.IsZero() && !now.Before(s.HardDeadline) {
			s.State = browserStateClosing
			s.closingSince = now
			s.notifyChange()
			continue
		}
		// Heartbeats: two misses past the interval turn a hosted session
		// orphaned, takeover or not.
		if s.State == browserStateHosted && !s.lastHeartbeat.IsZero() && now.Sub(s.lastHeartbeat) > r.heartbeatTimeout {
			s.State = browserStateOrphaned
			s.notifyChange()
		}
		// Idle TTL: paused while a person holds the lock.
		if s.Lock != browserLockHuman && !s.IdleExpiresAt.IsZero() && !now.Before(s.IdleExpiresAt) {
			s.State = browserStateClosing
			s.closingSince = now
			s.notifyChange()
		}
	}
}

// waitForAgentLock blocks while a person holds the lock, up to the shared
// budget. Read-only tools skip this entirely (SDD §7).
func (r *browserSessionRegistry) waitForAgentLock(ctx context.Context, sessionID string, budget time.Duration) (*browserSession, error) {
	deadline := time.NewTimer(budget)
	defer deadline.Stop()
	for {
		r.mu.Lock()
		s, ok := r.byID[sessionID]
		if !ok {
			r.mu.Unlock()
			return nil, errBrowserUnknownSession
		}
		switch s.State {
		case browserStateClosed:
			r.mu.Unlock()
			return s, errBrowserSessionClosed
		case browserStateOrphaned:
			r.mu.Unlock()
			return s, errBrowserSessionUnavailable
		}
		if s.Lock != browserLockHuman {
			r.mu.Unlock()
			return s, nil
		}
		wait := s.changeSignal()
		r.mu.Unlock()
		select {
		case <-wait:
		case <-deadline.C:
			return s, errBrowserLockHuman
		case <-ctx.Done():
			return s, ctx.Err()
		}
	}
}

var errBrowserSessionUnavailable = errors.New("browser session is unavailable")

// enqueueCommand queues one host action and waits for its result.
func (r *browserSessionRegistry) enqueueCommand(ctx context.Context, sessionID, action string, params map[string]any) (browserCommandResult, error) {
	commandID, err := newBrowserSessionID()
	if err != nil {
		return browserCommandResult{}, err
	}
	resultCh := make(chan browserCommandResult, 1)
	r.mu.Lock()
	s, ok := r.byID[sessionID]
	if !ok {
		r.mu.Unlock()
		return browserCommandResult{}, errBrowserUnknownSession
	}
	if s.State == browserStateClosed || s.State == browserStateClosing {
		r.mu.Unlock()
		return browserCommandResult{}, errBrowserSessionClosed
	}
	if s.State == browserStateRequested || s.State == browserStateOrphaned {
		r.mu.Unlock()
		return browserCommandResult{}, errBrowserSessionUnavailable
	}
	if s.pending == nil {
		s.pending = make(map[string]chan browserCommandResult)
	}
	s.pending[commandID] = resultCh
	s.commands = append(s.commands, browserCommand{ID: commandID, Action: action, Params: params})
	s.notifyCommand()
	r.mu.Unlock()

	select {
	case res := <-resultCh:
		return res, nil
	case <-ctx.Done():
		r.mu.Lock()
		delete(s.pending, commandID)
		r.mu.Unlock()
		return browserCommandResult{}, ctx.Err()
	}
}

// takeCommands is the host's long-poll: it hands over queued commands and
// applies the results the host reported for the previous batch.
func (r *browserSessionRegistry) takeCommands(ctx context.Context, sessionID string, results []browserCommandResult, budget time.Duration) ([]browserCommand, *browserSession, error) {
	r.mu.Lock()
	s, ok := r.byID[sessionID]
	if !ok {
		r.mu.Unlock()
		return nil, nil, errBrowserUnknownSession
	}
	applyResultsLocked(s, results)
	if len(s.commands) > 0 || s.State == browserStateClosed || s.State == browserStateClosing {
		commands := s.commands
		s.commands = nil
		r.mu.Unlock()
		return commands, s, nil
	}
	signal := s.notifier()
	r.mu.Unlock()

	deadline := time.NewTimer(budget)
	defer deadline.Stop()
	for {
		select {
		case <-signal:
		case <-deadline.C:
			r.mu.Lock()
			commands := s.commands
			s.commands = nil
			r.mu.Unlock()
			return commands, s, nil
		case <-ctx.Done():
			return nil, s, ctx.Err()
		}
		r.mu.Lock()
		applyResultsLocked(s, results)
		results = nil
		if len(s.commands) > 0 || s.State == browserStateClosed || s.State == browserStateClosing {
			commands := s.commands
			s.commands = nil
			r.mu.Unlock()
			return commands, s, nil
		}
		signal = s.notifier()
		r.mu.Unlock()
	}
}

func applyResultsLocked(s *browserSession, results []browserCommandResult) {
	for _, result := range results {
		ch, ok := s.pending[result.ID]
		if !ok {
			continue
		}
		delete(s.pending, result.ID)
		select {
		case ch <- result:
		default:
		}
	}
}

func (r *browserSessionRegistry) recordEvidence(sessionID, attachmentID string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	s, ok := r.byID[sessionID]
	if !ok {
		return
	}
	now := r.now()
	s.LastEvidence = &browserEvidence{AttachmentID: attachmentID, CapturedAt: now, ViewportOnly: true}
	s.notifyChange()
}

// windowView is the stable, externally visible session view (SDD §3.3).
// Nothing here may carry a query string, a token, or a local path.
type browserSessionView struct {
	SessionID     string             `json:"session_id"`
	TaskID        string             `json:"task_id"`
	IssueID       string             `json:"issue_id"`
	State         string             `json:"state"`
	LockOwner     string             `json:"lock_owner"`
	URL           browserSessionURL  `json:"url"`
	Partition     string             `json:"partition"`
	Capability    string             `json:"capability"`
	StartedAt     string             `json:"started_at"`
	IdleExpiresAt string             `json:"idle_expires_at,omitempty"`
	HardDeadline  string             `json:"hard_deadline,omitempty"`
	LastEvidence  *browserEvidence   `json:"last_evidence,omitempty"`
	Error         *browserSessionErr `json:"error,omitempty"`
}

type browserSessionErr struct {
	Code    string `json:"code"`
	Message string `json:"message"`
}

func (s *browserSession) view() browserSessionView {
	view := browserSessionView{
		SessionID:  s.ID,
		TaskID:     s.TaskID,
		IssueID:    s.IssueID,
		State:      string(s.State),
		LockOwner:  string(s.Lock),
		URL:        s.URL,
		Partition:  s.Partition,
		Capability: s.Capability,
		StartedAt:  s.StartedAt.UTC().Format(time.RFC3339),
	}
	if !s.IdleExpiresAt.IsZero() && s.State != browserStateClosed {
		view.IdleExpiresAt = s.IdleExpiresAt.UTC().Format(time.RFC3339)
	}
	if !s.HardDeadline.IsZero() && s.State != browserStateClosed {
		view.HardDeadline = s.HardDeadline.UTC().Format(time.RFC3339)
	}
	if s.LastEvidence != nil {
		evidence := *s.LastEvidence
		view.LastEvidence = &evidence
	}
	if s.ErrorCode != "" {
		view.Error = &browserSessionErr{Code: s.ErrorCode, Message: s.ErrorMessage}
	}
	return view
}

// runBrowserSessionSweeper drives sweep on a ticker until ctx is done.
func (r *browserSessionRegistry) runBrowserSessionSweeper(ctx context.Context, interval time.Duration) {
	if interval <= 0 {
		interval = defaultBrowserSweepInterval
	}
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			r.sweep()
		}
	}
}

// hostSessionDirective tells the host what to do with a session on the next
// heartbeat: destroy a view that is closing. It rides the heartbeat response
// because the contract has no separate push channel.
func browserHostDirective(s *browserSession) map[string]any {
	if s == nil {
		return nil
	}
	if s.State == browserStateClosing && !s.closedAcked {
		return map[string]any{"action": "close_view", "session_id": s.ID}
	}
	return nil
}

// browserTaskIDPattern is the only shape a task id may take before it reaches
// the registry.
//
// A task id arrives from a host-plane request body, and it used to be
// concatenated into the partition name, which later became a filesystem path.
// `..\..\Windows\Temp\victim-dir` passed every length check and made
// `filepath.Base(dir) == filepath.Base(partition)` true, so the daemon would
// have deleted whatever that path resolved to. Restricting the value here means
// the partition name is an identifier, never a path fragment.
var browserTaskIDPattern = regexp.MustCompile(`^[A-Za-z0-9_-]{1,128}$`)

// reusableBrowserState reports whether a session in this state may be handed to
// a caller again — reused by task, listed for discovery, or registered onto.
// `closing` is excluded everywhere: it is on its way to `closed`, and treating
// it as live is what let a finished session be revived.
func reusableBrowserState(state browserSessionState) bool {
	return state != browserStateClosed && state != browserStateClosing
}

// errBrowserInvalidTaskID lets transports answer 400 instead of 500.
var errBrowserInvalidTaskID = errors.New("invalid browser task id")

// validateBrowserTaskID reports whether a task id may enter the registry.
func validateBrowserTaskID(taskID string) error {
	if !browserTaskIDPattern.MatchString(taskID) {
		return fmt.Errorf("%w: task_id must be 1-128 characters from [A-Za-z0-9_-]", errBrowserInvalidTaskID)
	}
	return nil
}

// browserPartitionName derives the partition from a validated task id.
//
// Even with the identifier check above, the name is built from a hash rather
// than the raw value: the partition is handed to Desktop, echoed back on
// register, and would otherwise carry caller-controlled text into the
// Chromium partition namespace.
func browserPartitionName(taskID string) string {
	sum := sha256.Sum256([]byte(taskID))
	return "qa-task-" + hex.EncodeToString(sum[:8])
}
