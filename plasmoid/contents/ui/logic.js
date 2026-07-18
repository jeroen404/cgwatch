// cgwatch data/logic layer — pure functions only, no Qt APIs.
// Imported from QML as `import "logic.js" as Logic`; also runnable under node
// for the test harness in tools/test-logic.js (see the export guard at the
// end). Style mirrors knagger's contents/ui/logic.js: pure ES5 (var, no
// arrow functions, no template literals) so it loads unmodified in QML's JS
// engine as well as node.

// ---------------------------------------------------------------- utilities

function shq(s) {
    return "'" + String(s).replace(/'/g, "'\\''") + "'";
}

function numOr(v, dflt) {
    // Number(null) === 0 and Number("") === 0 -- neither is NaN, so both
    // would silently sail through the isNaN() check below and turn a
    // missing/blank threshold or cfg field into a real zero (e.g. a null
    // criticalPercent would make bucketize() treat every row as
    // critical). Reject those -- and undefined -- before coercing.
    if (v === null || v === undefined || v === "") return dflt;
    var n = Number(v);
    return isNaN(n) ? dflt : n;
}

function firstLine(s, maxLen) {
    var cap = maxLen || 300;
    var line = String(s === undefined || s === null ? "" : s).split("\n")[0].trim();
    return line.length > cap ? line.slice(0, cap) : line;
}

// ------------------------------------------------------------ helper plumbing

// cfg.helperCommand (like any other cfg override below) is trusted
// user/plasmoid config, not untrusted input from the helper or a cgroup
// name -- it is deliberately NOT shq()'d, so the user can put shell
// snippets (env vars, a wrapper script + args, ...) in helperCommand.
// Only unit/mem/cpu (data that round-trips through the helper/cgroup
// names) get shq()'d below.

// cfg: {helperCommand}. nonce: unique per instance+poll/action, alphanumeric
// /dash only — dedup-buster for the exec dataengine, which keys sources by
// the literal command string (two panel instances would otherwise coalesce
// onto the same DataSource).
function buildDumpCommand(cfg, nonce) {
    var prefix = "CGWATCH_POLL=" + nonce + " ";
    var helper = String((cfg && cfg.helperCommand) || "cgwatch-cli");
    return prefix + helper + " dump";
}

// mem/cpu: raw user-typed strings, or "" / null / undefined to leave that
// key untouched (mirrors the CLI's "omitted flag = None" contract). Flags
// use the "--mem=value"/"--cpu=value" equals form (not space-separated) so
// a value starting with "-" (e.g. "-5G") reaches argparse as one argv word
// and hits the helper's own parse_memory validation instead of being
// mistaken for a new flag (an argparse usage-error/exit-2 crash). isEdit
// mirrors the TUI's EditLimitsModal save path (see cgwatch/jsonapi.py's
// --edit): appends " --edit" when true.
function buildApplyCommand(unit, mem, cpu, cfg, nonce, isEdit) {
    var prefix = "CGWATCH_POLL=" + nonce + " ";
    var helper = String((cfg && cfg.helperCommand) || "cgwatch-cli");
    var cmd = prefix + helper + " apply " + shq(unit);
    if (mem) cmd += " --mem=" + shq(mem);
    if (cpu) cmd += " --cpu=" + shq(cpu);
    if (isEdit) cmd += " --edit";
    return cmd;
}

function buildUnlimitCommand(unit, cfg, nonce) {
    var prefix = "CGWATCH_POLL=" + nonce + " ";
    var helper = String((cfg && cfg.helperCommand) || "cgwatch-cli");
    return prefix + helper + " unlimit " + shq(unit);
}

// Human-readable hint for a parseHelperOutput() failure kind. "crash" and
// "error" normally carry their own specific detail (first stderr line /
// helper-reported message); this is the generic fallback used when no more
// specific text is available.
function errorHint(kind) {
    switch (kind) {
    case "missing": return "cgwatch-cli not found — check helperCommand in settings";
    case "crash":   return "cgwatch-cli crashed unexpectedly";
    case "badjson": return "cgwatch-cli returned invalid JSON";
    case "schema":  return "cgwatch-cli returned an unrecognized schema version";
    case "error":   return "cgwatch-cli reported an error";
    default:        return "Unexpected helper output";
    }
}

// Extract the best available message from an {"ok": false, ...} helper
// payload: dump failures carry error.message, apply/unlimit failures carry
// a messages[] array (straight from ApplyResult).
function helperErrorMessage(data) {
    if (data && data.error && data.error.message)
        return String(data.error.message);
    if (data && Array.isArray(data.messages) && data.messages.length)
        return data.messages.join("; ");
    return errorHint("error");
}

// Classify a completed helper invocation (Plasma5Support.DataSource "exited"
// signal gives stdout/stderr/exitCode separately — no curl-style status
// trailer to parse here, the helper's own JSON is the whole contract).
// expectedKind (optional): the command that was actually run ("dump" /
// "apply" / "unlimit" -- see CGWatchApi.qml's call sites). When given and
// the parsed payload's own "kind" doesn't match, that's version skew (an
// old/new helper replying with an unexpected shape) -- classified as the
// same "schema" kind as an outright schema-version mismatch, rather than
// risking the caller misreading e.g. a stale dump payload as an apply
// result.
// Returns {ok:true, kind:"dump"|"apply"|"unlimit", data} on success, or
// {ok:false, kind:"missing"|"crash"|"badjson"|"schema"|"error", hint} on
// any failure.
function parseHelperOutput(stdout, stderr, exitCode, expectedKind) {
    // The exec dataengine has been observed to hand back "exit code" as a
    // string in some Plasma versions; a bare string !== 0 comparison below
    // would then treat every clean exit as a crash.
    exitCode = Number(exitCode);
    if (exitCode === 127)
        return { ok: false, kind: "missing", hint: errorHint("missing") };
    if (exitCode !== 0) {
        var line = firstLine(stderr);
        return { ok: false, kind: "crash", hint: line || errorHint("crash") };
    }
    var out = String(stdout || "").trim();
    var data;
    try {
        data = JSON.parse(out);
    } catch (e) {
        return { ok: false, kind: "badjson", hint: errorHint("badjson") };
    }
    if (!data || typeof data !== "object")
        return { ok: false, kind: "badjson", hint: errorHint("badjson") };
    if (data.schema !== 1)
        return { ok: false, kind: "schema", hint: errorHint("schema") };
    if (expectedKind && data.kind !== expectedKind)
        return { ok: false, kind: "schema", hint: errorHint("schema") };
    if (data.ok === false)
        return { ok: false, kind: "error", hint: helperErrorMessage(data) };
    return { ok: true, kind: data.kind, data: data };
}

// ---------------------------------------------------------------- CPU deltas

// cgroup cpu period, microseconds (matches cgwatch/cgroup.py PERIOD_USEC).
var PERIOD_USEC = 100000;

// Mirrors CGroupCPUUsageHistory.get_last_cpu_usage_percent exactly for the
// "two valid samples, periods advanced" case, but — unlike the Python
// version, which folds "no history" and "counter went backwards" both into
// a bland 0.0 — deliberately tells those two apart from a real, verified
// zero: null means "no usable data yet" (first sample, or a counter reset
// e.g. from a service restart) so the UI can show "—" instead of a bogus
// percentage. 0 is returned only when periodsDiff is genuinely 0 (no
// periods elapsed since the last sample).
function cpuPercentFrom(prev, cur) {
    if (!prev || !cur) return null;
    var periodsDiff = numOr(cur.nr_periods, 0) - numOr(prev.nr_periods, 0);
    var usageDiff = numOr(cur.usage_usec, 0) - numOr(prev.usage_usec, 0);
    if (periodsDiff < 0 || usageDiff < 0) return null;   // counter reset
    if (periodsDiff === 0) return 0;
    return (usageDiff / (periodsDiff * PERIOD_USEC)) * 100;
}

// nr_throttled delta since the last sample. 0 when there is no previous
// sample (first poll) or the counter went backwards (reset); never negative.
function throttledDelta(prev, cur) {
    if (!prev || !cur) return 0;
    var d = numOr(cur.nr_throttled, 0) - numOr(prev.nr_throttled, 0);
    return d < 0 ? 0 : d;
}

// --------------------------------------------------------- action replay

// Failure-survives-popup-close (Q4): decide whether an unconsumed failed
// action result should be surfaced for the editor/page identified by
// ctxKey (a row's model.key, or main.qml's addPageContextKey sentinel for
// the add-service page), and if so mark it consumed so it isn't replayed
// again. rootItem: main.qml's root item, exposing (and here mutating in
// place) lastActionConsumed/pendingActionKey/lastActionResult -- see its
// requestApply()/requestUnlimit()/handleActionFinished().
//
// Returns the hint string to display, or null when nothing should be
// shown: a still-in-flight/never-run action, a *successful* result (never
// surfaced as an error), or a result belonging to a different key (e.g.
// another row's edit, or the add-service page) must NOT surface here.
//
// Callers: CGroupDelegate's onEditorOpenChanged (ctxKey = model.key) and
// its Connections.onActionResultSeqChanged; AddServicePage's reset()
// (ctxKey = rootItem.addPageContextKey) and its own
// Connections.onActionResultSeqChanged. The two call sites differ only in
// what they do when this returns null (onEditorOpenChanged/reset() clear
// their local error text; the live Connections handlers leave it alone).
function consumeActionError(rootItem, ctxKey) {
    if (!rootItem.lastActionConsumed && rootItem.pendingActionKey === ctxKey
        && rootItem.lastActionResult && !rootItem.lastActionResult.ok) {
        rootItem.lastActionConsumed = true
        return rootItem.lastActionResult.hint || "action failed"
    }
    return null
}

// ------------------------------------------------------------------- rows

// dumpData: a parsed "dump" payload (schema 1, ok:true). prevSamples: the
// `samples` map returned by the previous computeRows() call (or {} / null
// on the first poll). cfg is currently unused here (severity bucketing is a
// separate step in bucketize()/memSevName()) but is accepted for symmetry
// with the other cfg-taking functions and future-proofing.
//
// Returns {rows, samples}: rows sorted by memory_percent descending, every
// field present and never null/undefined (ListModel fixes its role types
// from the first append — a role that is sometimes null and sometimes a
// number breaks binding). Fields that have no meaningful value this poll
// (no CPU quota, no previous sample) use the sentinel -1 rather than null.
function computeRows(dumpData, prevSamples, cfg) {
    var prev = prevSamples || {};
    var cgroups = (dumpData && dumpData.cgroups) || [];
    var rows = [];
    var samples = {};
    for (var i = 0; i < cgroups.length; i++) {
        var cg = cgroups[i] || {};
        var key = String(cg.name || "");
        var stat = cg.cpu_stat || {};
        var cur = {
            usage_usec: numOr(stat.usage_usec, 0),
            nr_periods: numOr(stat.nr_periods, 0),
            nr_throttled: numOr(stat.nr_throttled, 0)
        };
        var p = prev[key];
        var cpuPct = cpuPercentFrom(p, cur);
        var prefill = cg.edit_prefill || {};
        rows.push({
            name: key,
            unit: String(cg.unit || ""),
            short_name: String(cg.short_name || ""),
            description: String(cg.description || ""),
            memory_current: numOr(cg.memory_current, 0),
            memory_max: (cg.memory_max === null || cg.memory_max === undefined) ? -1 : numOr(cg.memory_max, -1),
            memory_percent: numOr(cg.memory_percent, 0),
            memory_effective: numOr(cg.memory_effective, 0),
            memory_cache: numOr(cg.memory_cache, 0),
            cpu_quota_percent: (cg.cpu_quota_percent === null || cg.cpu_quota_percent === undefined) ? -1 : numOr(cg.cpu_quota_percent, -1),
            usage_usec: cur.usage_usec,
            nr_periods: cur.nr_periods,
            nr_throttled: cur.nr_throttled,
            throttled_usec: numOr(stat.throttled_usec, 0),
            cpu_percent: cpuPct === null ? -1 : cpuPct,
            throttled_delta: throttledDelta(p, cur),
            edit_prefill_memory: String(prefill.memory || ""),
            edit_prefill_cpu: String(prefill.cpu || "")
        });
        samples[key] = cur;
    }
    // Secondary tie-break by name: without it, rows with equal
    // memory_percent (e.g. several at exactly 0%) reorder from poll to
    // poll purely from listdir/object-iteration jitter, since Array.sort
    // is not guaranteed stable for equal keys across engines.
    rows.sort(function (a, b) {
        var d = b.memory_percent - a.memory_percent;
        if (d !== 0) return d;
        if (a.name < b.name) return -1;
        if (a.name > b.name) return 1;
        return 0;
    });
    return { rows: rows, samples: samples };
}

// -------------------------------------------------------------- bucketing

// >= semantics: a row at exactly warnPct is "warning", at exactly critPct is
// "critical" (critical takes priority — checked first). Throttle count is
// independent of the memory bucket a row falls into.
function bucketize(rows, warnPct, critPct) {
    var warn = numOr(warnPct, 80);
    var crit = numOr(critPct, 90);
    var result = {
        total: rows.length,
        criticalCount: 0,
        warningCount: 0,
        calmCount: 0,
        throttledCount: 0,
        worstOverallPercent: 0,
        worstCriticalPercent: 0,
        worstWarningPercent: 0
    };
    for (var i = 0; i < rows.length; i++) {
        var r = rows[i];
        var pct = numOr(r.memory_percent, 0);
        if (pct > result.worstOverallPercent) result.worstOverallPercent = pct;
        if (pct >= crit) {
            result.criticalCount++;
            if (pct > result.worstCriticalPercent) result.worstCriticalPercent = pct;
        } else if (pct >= warn) {
            result.warningCount++;
            if (pct > result.worstWarningPercent) result.worstWarningPercent = pct;
        } else {
            result.calmCount++;
        }
        if (numOr(r.throttled_delta, 0) > 0) result.throttledCount++;
    }
    return result;
}

// cfg: {warningPercent, criticalPercent}. Same >= semantics as bucketize().
function memSevName(pct, cfg) {
    var warn = numOr(cfg && cfg.warningPercent, 80);
    var crit = numOr(cfg && cfg.criticalPercent, 90);
    var p = numOr(pct, 0);
    if (p >= crit) return "critical";
    if (p >= warn) return "warning";
    return "calm";
}

// ------------------------------------------------------------- formatting

// Decimal (SI, base-1000) byte formatting, matching python humanize's
// naturalsize(value) default (binary=False, format="%.1f"):
//   naturalsize(0)          -> "0 Bytes"
//   naturalsize(1)          -> "1 Byte"
//   naturalsize(500000000)  -> "500.0 MB"
//   naturalsize(1234567890) -> "1.2 GB"
var BYTE_SUFFIXES = ["kB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB", "RB", "QB"];

function humanBytes(bytes) {
    var b = numOr(bytes, 0);
    var abs = Math.abs(b);
    if (abs === 1) return Math.trunc(b) + " Byte";
    if (abs < 1000) return Math.trunc(b) + " Bytes";
    var i = BYTE_SUFFIXES.length + 1;          // index into 1000^i, i starts at 2
    var suffix = BYTE_SUFFIXES[BYTE_SUFFIXES.length - 1];
    for (var idx = 0; idx < BYTE_SUFFIXES.length; idx++) {
        var unit = Math.pow(1000, idx + 2);
        if (abs < unit) {
            i = idx + 2;
            suffix = BYTE_SUFFIXES[idx];
            break;
        }
    }
    var unitFinal = Math.pow(1000, i);
    var val = 1000 * (b / unitFinal);
    return val.toFixed(1) + " " + suffix;
}

// node test harness support (harmless under QML: `module` is undefined there)
if (typeof module !== "undefined" && module.exports) {
    module.exports = {
        shq: shq,
        numOr: numOr,
        buildDumpCommand: buildDumpCommand,
        buildApplyCommand: buildApplyCommand,
        buildUnlimitCommand: buildUnlimitCommand,
        errorHint: errorHint,
        helperErrorMessage: helperErrorMessage,
        parseHelperOutput: parseHelperOutput,
        PERIOD_USEC: PERIOD_USEC,
        cpuPercentFrom: cpuPercentFrom,
        throttledDelta: throttledDelta,
        consumeActionError: consumeActionError,
        computeRows: computeRows,
        bucketize: bucketize,
        memSevName: memSevName,
        humanBytes: humanBytes
    };
}
