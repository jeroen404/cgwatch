#!/usr/bin/env node
// Sanity tests for contents/ui/logic.js (pure functions, no Qt).
// Run: node tools/test-logic.js
"use strict";

const path = require("path");
const fs = require("fs");
const { execFileSync } = require("child_process");
const L = require(path.join(__dirname, "..", "contents", "ui", "logic.js"));

let failures = 0;
function ok(cond, name) {
    if (cond) { console.log("  ok  " + name); }
    else { failures++; console.error("FAIL  " + name); }
}
function eq(actual, expected, name) {
    const same = JSON.stringify(actual) === JSON.stringify(expected);
    ok(same, name + (same ? "" :
       `  (got ${JSON.stringify(actual)}, want ${JSON.stringify(expected)})`));
}

const FIXDIR = path.join(__dirname, "..", "fixtures");

function fixtureText(name) {
    return fs.readFileSync(path.join(FIXDIR, name), "utf8");
}
function fixture(name) {
    return JSON.parse(fixtureText(name));
}
// Mirrors mock-cgwatch-cli.sh's sed substitution: throttled.json/many.json
// carry bare @USAGE@/@PERIODS@/@THROTTLED@ tokens in one cgroup's cpu.stat
// so they aren't valid JSON until substituted — shared fixtures, shared
// substitution logic, so the mock and the tests can't drift apart.
function substituteTokens(text, usage, periods, throttled) {
    return text
        .replace(/@USAGE@/g, String(usage))
        .replace(/@PERIODS@/g, String(periods))
        .replace(/@THROTTLED@/g, String(throttled));
}
function templatedFixture(name, usage, periods, throttled) {
    return JSON.parse(substituteTokens(fixtureText(name), usage, periods, throttled));
}

const CFG = { helperCommand: "cgwatch-cli" };

// ---- fixtures directory sanity scan ----
// Every *.json fixture must be valid JSON on its own, EXCEPT throttled.json
// and many.json, which intentionally carry bare (unquoted) placeholder
// tokens and only become valid JSON after substitution.
{
    const TOKENIZED = new Set(["throttled.json", "many.json"]);
    const names = fs.readdirSync(FIXDIR).filter((n) => n.endsWith(".json"));
    ok(names.length >= 9, "fixtures dir has the expected .json fixtures (found " + names.length + ")");
    for (const name of names) {
        const text = fixtureText(name);
        if (TOKENIZED.has(name)) {
            let threw = false;
            try { JSON.parse(text); } catch (e) { threw = true; }
            ok(threw, name + ": raw JSON.parse fails (bare placeholder tokens), as designed");
            let parsedOk = true;
            try { JSON.parse(substituteTokens(text, 1, 1, 1)); } catch (e) { parsedOk = false; }
            ok(parsedOk, name + ": parses after token substitution");
        } else {
            let parsedOk = true;
            try { JSON.parse(text); } catch (e) { parsedOk = false; }
            ok(parsedOk, name + ": parses as plain JSON");
        }
    }
    // badjson.txt is deliberately truncated and deliberately not *.json
    // (so it doesn't trip the scan above); exercised via parseHelperOutput.
    ok(fs.existsSync(path.join(FIXDIR, "badjson.txt")), "badjson.txt fixture present");
}

// ---- shq (verbatim from knagger; load-bearing for unit names with \ and @) ----
{
    const nasty = `a'b"c$(rm -rf /)\`x\` \\ $HOME !`;
    const echoed = execFileSync("/bin/sh", ["-c", "printf %s " + L.shq(nasty)]).toString();
    eq(echoed, nasty, "shq survives a real /bin/sh round-trip (adversarial string)");

    const unitName = "app-firefox\\x2desr@3e2f8b91.service";
    const echoedUnit = execFileSync("/bin/sh", ["-c", "printf %s " + L.shq(unitName)]).toString();
    eq(echoedUnit, unitName, "shq survives a real unit name with a literal backslash escape");
}

// ---- buildDumpCommand / buildUnlimitCommand ----
{
    const cmd = L.buildDumpCommand(CFG, "tok-7");
    eq(cmd, "CGWATCH_POLL=tok-7 cgwatch-cli dump", "dump command: nonce prefix + helper + subcommand");

    const cmdDefault = L.buildDumpCommand({}, "tok-8");
    eq(cmdDefault, "CGWATCH_POLL=tok-8 cgwatch-cli dump", "dump command falls back to cgwatch-cli when helperCommand unset");

    const un = L.buildUnlimitCommand("app-firefox\\x2desr@.service", CFG, "tok-9");
    eq(un, "CGWATCH_POLL=tok-9 cgwatch-cli unlimit " + L.shq("app-firefox\\x2desr@.service"),
       "unlimit command shape");
}

// ---- buildApplyCommand: flag omission + real-shell argv capture ----
{
    const noFlags = L.buildApplyCommand("app-code@.service", null, undefined, CFG, "t-1");
    eq(noFlags, "CGWATCH_POLL=t-1 cgwatch-cli apply " + L.shq("app-code@.service"),
       "apply command omits --mem/--cpu when both are null/undefined");

    const emptyFlags = L.buildApplyCommand("app-code@.service", "", "", CFG, "t-2");
    eq(emptyFlags, "CGWATCH_POLL=t-2 cgwatch-cli apply " + L.shq("app-code@.service"),
       "apply command omits --mem/--cpu when both are empty strings");

    const memOnly = L.buildApplyCommand("app-code@.service", "2G", null, CFG, "t-3");
    ok(memOnly.indexOf(" --mem=" + L.shq("2G")) !== -1, "apply command includes --mem= (equals form) when given");
    ok(memOnly.indexOf("--cpu") === -1, "apply command omits --cpu when not given");

    // argv capture through a real shell: replace the (fixed, known) nonce +
    // helper prefix with a fake function so we can see exactly what the
    // helper process would receive, unit/mem/cpu quoting intact.
    const nastyUnit = "app-firefox\\x2desr@3e2f8b91.service";
    const nastyMem = "a'b\"c$(rm -rf /)\\ 2G";
    const nastyCpu = "200% `x`";
    const cmd = L.buildApplyCommand(nastyUnit, nastyMem, nastyCpu, CFG, "tok-argv");
    ok(cmd.indexOf("CGWATCH_POLL=tok-argv cgwatch-cli apply ") === 0, "apply cmd has nonce+helper prefix");
    const script = "fakehelper() { for a in \"$@\"; do printf '%s\\n' \"$a\"; done; }; "
                 + cmd.replace(/^CGWATCH_POLL=\S+ cgwatch-cli /, "fakehelper ");
    const out = execFileSync("/bin/sh", ["-c", script]).toString();
    const argv = out.replace(/\n$/, "").split("\n");
    // Equals form: --mem/--cpu and their value arrive as ONE argv word each
    // (not two separate words like the old space-separated form).
    eq(argv, ["apply", nastyUnit, "--mem=" + nastyMem, "--cpu=" + nastyCpu],
       "apply command argv survives the shell intact (adversarial unit/mem/cpu, equals form)");

    // Hyphen-leading value (e.g. a mistyped/negative MemoryMax): the equals
    // form keeps flag+value as one argv word ("--mem=-5G"), so argparse
    // parses it and the helper's own parse_memory() produces a friendly
    // validation error -- instead of argparse mistaking "-5G" for a new
    // (unrecognized) flag and exiting 2 with a usage-error crash.
    const hyphenCmd = L.buildApplyCommand("app-code@.service", "-5G", null, CFG, "t-hyphen");
    const hyphenScript = "fakehelper() { for a in \"$@\"; do printf '%s\\n' \"$a\"; done; }; "
                        + hyphenCmd.replace(/^CGWATCH_POLL=\S+ cgwatch-cli /, "fakehelper ");
    const hyphenOut = execFileSync("/bin/sh", ["-c", hyphenScript]).toString();
    const hyphenArgv = hyphenOut.replace(/\n$/, "").split("\n");
    eq(hyphenArgv, ["apply", "app-code@.service", "--mem=-5G"],
       "apply command: hyphen-leading value survives as one argv word --mem=-5G");

    // isEdit (P3 parity): appends --edit when true, omitted otherwise.
    const editCmd = L.buildApplyCommand("app-code@.service", "2G", null, CFG, "t-edit", true);
    ok(editCmd.indexOf(" --edit") !== -1, "apply command appends --edit when isEdit is true");
    const noEditCmd = L.buildApplyCommand("app-code@.service", "2G", null, CFG, "t-noedit", false);
    ok(noEditCmd.indexOf("--edit") === -1, "apply command omits --edit when isEdit is false");
    const omittedEditCmd = L.buildApplyCommand("app-code@.service", "2G", null, CFG, "t-noedit2");
    ok(omittedEditCmd.indexOf("--edit") === -1, "apply command omits --edit when isEdit is omitted");
}

// ---- numOr (J1): null/undefined/""/NaN all fall back to the default --
// Number(null)===0 and Number("")===0 would otherwise turn a missing
// threshold/cfg field into a real (and dangerously wrong) zero. ----
{
    eq(L.numOr(null, 80), 80, "numOr: null -> default");
    eq(L.numOr(undefined, 80), 80, "numOr: undefined -> default");
    eq(L.numOr("", 80), 80, "numOr: empty string -> default");
    eq(L.numOr(NaN, 80), 80, "numOr: NaN -> default");
    eq(L.numOr(0, 80), 0, "numOr: real zero passes through untouched");
    eq(L.numOr("42", 80), 42, "numOr: numeric string still coerces");
    eq(L.numOr(42, 80), 42, "numOr: real number passes through untouched");

    // Integration: a literal null threshold/cfg must not turn everything
    // critical (Number(null) === 0 <= any real memory_percent).
    const rows = [{ memory_percent: 50, throttled_delta: 0 }];
    eq(L.bucketize(rows, null, null).criticalCount, 0,
       "bucketize: null thresholds fall back to 80/90 defaults, not 0");
    eq(L.bucketize(rows, null, null).calmCount, 1,
       "bucketize: null thresholds -> row correctly bucketed as calm");
    eq(L.memSevName(50, null), "calm", "memSevName: null cfg falls back to defaults");
    eq(L.memSevName(50, { warningPercent: null, criticalPercent: null }), "calm",
       "memSevName: null threshold fields in cfg fall back to defaults");
}

// ---- parseHelperOutput ----
{
    const missing = L.parseHelperOutput("", "", 127);
    ok(!missing.ok && missing.kind === "missing", "exit 127 -> missing");

    const crash = L.parseHelperOutput("", "Traceback (most recent call last):\n  File x\nValueError: boom", 1);
    ok(!crash.ok && crash.kind === "crash", "exit 1 -> crash");
    eq(crash.hint, "Traceback (most recent call last):", "crash hint is the first stderr line");

    const crashNoStderr = L.parseHelperOutput("", "", 1);
    ok(!crashNoStderr.ok && crashNoStderr.kind === "crash", "exit 1 with empty stderr -> crash");
    eq(crashNoStderr.hint, L.errorHint("crash"), "crash hint falls back to errorHint when stderr is empty");

    const badjson = L.parseHelperOutput(fixtureText("badjson.txt"), "", 0);
    ok(!badjson.ok && badjson.kind === "badjson", "truncated JSON -> badjson");

    const badjsonEmpty = L.parseHelperOutput("", "", 0);
    ok(!badjsonEmpty.ok && badjsonEmpty.kind === "badjson", "empty stdout -> badjson");

    const schema = L.parseHelperOutput('{"schema":2,"kind":"dump","ok":true}', "", 0);
    ok(!schema.ok && schema.kind === "schema", "schema skew -> schema");

    const dumpErr = L.parseHelperOutput(
        '{"schema":1,"kind":"dump","ok":false,"error":{"kind":"cgroups-unavailable","message":"no /sys/fs/cgroup"}}',
        "", 0);
    ok(!dumpErr.ok && dumpErr.kind === "error", "dump ok:false -> error");
    eq(dumpErr.hint, "no /sys/fs/cgroup", "dump error hint comes from error.message");

    const applyErr = L.parseHelperOutput(fixtureText("apply-fail.json"), "", 0);
    ok(!applyErr.ok && applyErr.kind === "error", "apply ok:false fixture -> error");
    eq(applyErr.hint, "invalid memory value", "apply error hint comes from messages[]");

    const noDetail = L.parseHelperOutput('{"schema":1,"kind":"apply","ok":false}', "", 0);
    eq(noDetail.hint, L.errorHint("error"), "error hint falls back to errorHint when no detail is present");

    const goodDump = L.parseHelperOutput(fixtureText("calm.json"), "", 0);
    ok(goodDump.ok && goodDump.kind === "dump", "good dump fixture -> ok, kind dump");
    eq(goodDump.data, fixture("calm.json"), "good dump fixture -> data matches parsed fixture");

    const goodApply = L.parseHelperOutput(fixtureText("apply-ok.json"), "", 0);
    ok(goodApply.ok && goodApply.kind === "apply", "good apply fixture -> ok, kind apply");

    const goodUnlimit = L.parseHelperOutput(fixtureText("unlimit-ok.json"), "", 0);
    ok(goodUnlimit.ok && goodUnlimit.kind === "unlimit", "good unlimit fixture -> ok, kind unlimit");

    const goodWarn = L.parseHelperOutput(fixtureText("apply-warn.json"), "", 0);
    ok(goodWarn.ok && goodWarn.data.messages.length === 1, "apply-warn fixture -> ok:true with a warning message");

    // exitCode coercion (J3): the exec dataengine has been observed to hand
    // back a string exit code in some Plasma versions -- "0" must not be
    // treated as truthy/!==0 (which would misclassify every clean exit as
    // a crash), and "127" must still trip the missing-helper branch.
    const strZero = L.parseHelperOutput(fixtureText("calm.json"), "", "0");
    ok(strZero.ok && strZero.kind === "dump", "exitCode as string '0' -> still treated as success");
    const strMissing = L.parseHelperOutput("", "", "127");
    ok(!strMissing.ok && strMissing.kind === "missing", "exitCode as string '127' -> still 'missing'");

    // expectedKind (J3): version-skew handling -- a parsed payload whose own
    // "kind" doesn't match the command that was actually run classifies as
    // "schema", same as an outright schema-version mismatch.
    const kindMismatch = L.parseHelperOutput(fixtureText("apply-ok.json"), "", 0, "dump");
    ok(!kindMismatch.ok && kindMismatch.kind === "schema",
       "parseHelperOutput: kind mismatch against expectedKind -> schema");
    const kindMatch = L.parseHelperOutput(fixtureText("apply-ok.json"), "", 0, "apply");
    ok(kindMatch.ok && kindMatch.kind === "apply",
       "parseHelperOutput: kind matches expectedKind -> normal success handling");
    const noExpectedKind = L.parseHelperOutput(fixtureText("apply-ok.json"), "", 0);
    ok(noExpectedKind.ok && noExpectedKind.kind === "apply",
       "parseHelperOutput: expectedKind omitted -> no kind check performed");
}

// ---- CPU delta math (mirrors CGroupCPUUsageHistory.get_last_cpu_usage_percent) ----
{
    ok(L.cpuPercentFrom(null, { usage_usec: 100, nr_periods: 1 }) === null,
       "cpuPercentFrom: no prev sample -> null (first poll)");
    ok(L.cpuPercentFrom(undefined, { usage_usec: 100, nr_periods: 1 }) === null,
       "cpuPercentFrom: undefined prev -> null");

    const prev = { usage_usec: 1000000, nr_periods: 100 };
    const cur = { usage_usec: 1350000, nr_periods: 105 };
    eq(L.cpuPercentFrom(prev, cur), 70, "cpuPercentFrom: exact percent (350000us / (5*100000us) * 100 = 70)");

    ok(L.cpuPercentFrom({ usage_usec: 500000, nr_periods: 50 }, { usage_usec: 100000, nr_periods: 60 }) === null,
       "cpuPercentFrom: negative usage delta (counter reset) -> null");
    ok(L.cpuPercentFrom({ usage_usec: 100, nr_periods: 80 }, { usage_usec: 200, nr_periods: 70 }) === null,
       "cpuPercentFrom: negative periods delta (counter reset) -> null");

    eq(L.cpuPercentFrom({ usage_usec: 100, nr_periods: 50 }, { usage_usec: 100, nr_periods: 50 }), 0,
       "cpuPercentFrom: no periods elapsed -> 0, not null (idle no-quota cgroup)");
    eq(L.cpuPercentFrom({ usage_usec: 100, nr_periods: 50 }, { usage_usec: 99999, nr_periods: 50 }), 0,
       "cpuPercentFrom: periods unchanged even with usage movement -> 0 (matches Python's usec_passed<=0 branch)");

    eq(L.throttledDelta(null, { nr_throttled: 5 }), 0, "throttledDelta: no prev -> 0");
    eq(L.throttledDelta({ nr_throttled: 10 }, { nr_throttled: 15 }), 5, "throttledDelta: normal increase");
    eq(L.throttledDelta({ nr_throttled: 20 }, { nr_throttled: 12 }), 0, "throttledDelta: counter reset clamps to 0");
}

// ---- bucketize: >= boundaries at exactly 80/90, throttled independent ----
{
    const rows = [
        { memory_percent: 79.9, throttled_delta: 0 },
        { memory_percent: 80,   throttled_delta: 3 },   // exact warning boundary
        { memory_percent: 89.9, throttled_delta: 0 },
        { memory_percent: 90,   throttled_delta: 0 },   // exact critical boundary
        { memory_percent: 95,   throttled_delta: 0 },
    ];
    const b = L.bucketize(rows, 80, 90);
    eq([b.calmCount, b.warningCount, b.criticalCount], [1, 2, 2], "bucketize: >= semantics at both boundaries");
    eq(b.worstOverallPercent, 95, "bucketize: worst overall");
    eq(b.worstWarningPercent, 89.9, "bucketize: worst warning");
    eq(b.worstCriticalPercent, 95, "bucketize: worst critical");
    eq(b.throttledCount, 1, "bucketize: throttled counted independently of memory bucket");
    eq(b.total, 5, "bucketize: total row count");
}

// ---- memSevName ----
{
    const cfg = { warningPercent: 80, criticalPercent: 90 };
    eq(L.memSevName(79.9, cfg), "calm", "memSevName: below warning");
    eq(L.memSevName(80, cfg), "warning", "memSevName: exact warning boundary");
    eq(L.memSevName(89.9, cfg), "warning", "memSevName: below critical");
    eq(L.memSevName(90, cfg), "critical", "memSevName: exact critical boundary");
    eq(L.memSevName(45, {}), "calm", "memSevName: falls back to 80/90 defaults with empty cfg");
}

// ---- computeRows: no-undefined/no-null invariant, sorting, sentinels ----
const ROW_FIELDS = [
    "name", "unit", "short_name", "description", "memory_current", "memory_max",
    "memory_percent", "cpu_quota_percent", "usage_usec", "nr_periods", "nr_throttled",
    "throttled_usec", "cpu_percent", "throttled_delta", "edit_prefill_memory", "edit_prefill_cpu",
];
function assertRowsWellFormed(rows, label) {
    for (const row of rows) {
        for (const field of ROW_FIELDS) {
            const has = Object.prototype.hasOwnProperty.call(row, field);
            ok(has && row[field] !== undefined && row[field] !== null,
               label + ": row " + JSON.stringify(row.name) + " field '" + field + "' present and non-null");
        }
    }
}
{
    for (const name of ["calm.json", "warning.json", "critical.json"]) {
        const dump = fixture(name);
        const { rows, samples } = L.computeRows(dump, null, {});
        assertRowsWellFormed(rows, name);
        eq(rows.length, dump.cgroups.length, name + ": row count matches cgroup count");
        const sorted = rows.every((r, i) => i === 0 || rows[i - 1].memory_percent >= r.memory_percent);
        ok(sorted, name + ": rows sorted by memory_percent descending");
        eq(Object.keys(samples).length, dump.cgroups.length, name + ": samples map has one entry per cgroup");
        // first poll: no CPU history yet
        ok(rows.every((r) => r.cpu_percent === -1), name + ": first poll -> cpu_percent sentinel -1 for all rows");
        ok(rows.every((r) => r.throttled_delta === 0), name + ": first poll -> throttled_delta 0 for all rows");
    }

    // sentinel substitution: -1 for null memory_max/cpu_quota_percent
    const calm = fixture("calm.json");
    const { rows: calmRows } = L.computeRows(calm, null, {});
    const dolphin = calmRows.find((r) => r.short_name === "org.kde.dolphin");
    ok(dolphin && dolphin.cpu_quota_percent === -1, "computeRows: null cpu_quota_percent -> -1 sentinel");
    const syncthing = calmRows.find((r) => r.name === "syncthing.service");
    ok(syncthing && syncthing.cpu_quota_percent === 50, "computeRows: real cpu_quota_percent passed through");

    // J4: secondary tie-break by name when memory_percent is equal --
    // otherwise rows at e.g. exactly 0% reorder from poll to poll purely
    // from listdir/object-iteration jitter.
    const tieDump = {
        cgroups: [
            { name: "app-zeta@1.service", memory_percent: 0, cpu_stat: {} },
            { name: "app-alpha@1.service", memory_percent: 0, cpu_stat: {} },
            { name: "app-mid@1.service", memory_percent: 0, cpu_stat: {} },
        ]
    };
    const { rows: tieRows } = L.computeRows(tieDump, null, {});
    eq(tieRows.map((r) => r.name), ["app-alpha@1.service", "app-mid@1.service", "app-zeta@1.service"],
       "computeRows: equal memory_percent rows tie-break sorted by name ascending");

    // second poll with unchanged counters -> cpu_percent 0 (periods didn't
    // move), throttled_delta 0, never a stale -1/undefined
    const { samples: calmSamples } = L.computeRows(calm, null, {});
    const { rows: rows2 } = L.computeRows(calm, calmSamples, {});
    assertRowsWellFormed(rows2, "calm.json (2nd poll, unchanged)");
    ok(rows2.every((r) => r.cpu_percent === 0), "computeRows: unchanged counters on 2nd poll -> cpu_percent 0");
    ok(rows2.every((r) => r.throttled_delta === 0), "computeRows: unchanged counters on 2nd poll -> throttled_delta 0");

    // manufactured real CPU movement between two polls
    const calm2 = JSON.parse(JSON.stringify(calm));
    const target = calm2.cgroups.find((c) => c.name === "syncthing.service");
    target.cpu_stat.usage_usec += 700000;   // +700000us
    target.cpu_stat.nr_periods += 10;       // +10 periods (=> 1000000us elapsed)
    target.cpu_stat.nr_throttled += 4;
    const { rows: rows3 } = L.computeRows(calm2, calmSamples, {});
    const syncthing3 = rows3.find((r) => r.name === "syncthing.service");
    eq(syncthing3.cpu_percent, 70, "computeRows: live CPU% matches cpuPercentFrom exactly (70%)");
    eq(syncthing3.throttled_delta, 4, "computeRows: live throttled_delta matches throttledDelta exactly");

    // ---- throttled.json / many.json: tokenized fixture round-trip over two synthetic polls ----
    for (const name of ["throttled.json", "many.json"]) {
        const poll1 = templatedFixture(name, 1000000, 100, 5);
        const poll2 = templatedFixture(name, 1350000, 105, 9);   // +350000us usage, +5 periods, +4 throttled
        const { samples: s1 } = L.computeRows(poll1, null, {});
        const { rows: rows2t } = L.computeRows(poll2, s1, {});
        assertRowsWellFormed(rows2t, name + " (2nd poll)");
        const tokenizedName = name === "throttled.json"
            ? "app-firefox\\x2desr@3e2f8b91.service"
            : "app-steam@abcdef01.service";
        const tokenRow = rows2t.find((r) => r.name === tokenizedName);
        eq(tokenRow.cpu_percent, 70, name + ": tokenized cgroup live cpu_percent exact (70%)");
        eq(tokenRow.throttled_delta, 4, name + ": tokenized cgroup live throttled_delta exact");
        // every other (untouched) row saw no counter movement between polls
        for (const r of rows2t) {
            if (r.name === tokenizedName) continue;
            ok(r.cpu_percent === 0, name + ": untouched row '" + r.name + "' cpu_percent 0 on 2nd poll");
            ok(r.throttled_delta === 0, name + ": untouched row '" + r.name + "' throttled_delta 0 on 2nd poll");
        }
    }
}

// ---- humanBytes: decimal units, matches python humanize.naturalsize exactly ----
{
    eq(L.humanBytes(0), "0 Bytes", "humanBytes: 0");
    eq(L.humanBytes(1), "1 Byte", "humanBytes: 1 (singular)");
    eq(L.humanBytes(2), "2 Bytes", "humanBytes: 2");
    eq(L.humanBytes(999), "999 Bytes", "humanBytes: 999");
    eq(L.humanBytes(1000), "1.0 kB", "humanBytes: 1000 (kB boundary)");
    eq(L.humanBytes(1023), "1.0 kB", "humanBytes: 1023");
    eq(L.humanBytes(1024), "1.0 kB", "humanBytes: 1024");
    eq(L.humanBytes(999999), "1000.0 kB", "humanBytes: 999999 (rounds up but stays kB, matches humanize quirk)");
    eq(L.humanBytes(1000000), "1.0 MB", "humanBytes: 1000000");
    eq(L.humanBytes(500000000), "500.0 MB", "humanBytes: 500000000");
    eq(L.humanBytes(524288000), "524.3 MB", "humanBytes: 524288000");
    eq(L.humanBytes(999999999), "1000.0 MB", "humanBytes: 999999999 (rounds up but stays MB)");
    eq(L.humanBytes(1000000000), "1.0 GB", "humanBytes: 1000000000");
    eq(L.humanBytes(1234567890), "1.2 GB", "humanBytes: 1234567890");
    eq(L.humanBytes(1500000000), "1.5 GB", "humanBytes: 1500000000");
    eq(L.humanBytes(2147483648), "2.1 GB", "humanBytes: 2147483648 (2 GiB)");
    eq(L.humanBytes(3298534883328), "3.3 TB", "humanBytes: 3298534883328 (3 TiB)");
    // J5: RB/QB (ronna-/quetta-bytes) suffixes, added in humanize 4.x.
    eq(L.humanBytes(1e27), "1.0 RB", "humanBytes: 10^27 -> RB (humanize 4.x)");
    eq(L.humanBytes(1e30), "1.0 QB", "humanBytes: 10^30 -> QB (humanize 4.x)");
}

// ---- errorHint / helperErrorMessage ----
{
    eq(L.errorHint("missing"), "cgwatch-cli not found — check helperCommand in settings", "errorHint: missing");
    ok(L.errorHint("nonsense-kind").length > 0, "errorHint: unknown kind still returns a non-empty string");
    eq(L.helperErrorMessage({ error: { message: "boom" } }), "boom", "helperErrorMessage: error.message wins");
    eq(L.helperErrorMessage({ messages: ["a", "b"] }), "a; b", "helperErrorMessage: messages[] joined");
    eq(L.helperErrorMessage({}), L.errorHint("error"), "helperErrorMessage: falls back to errorHint('error')");
}

// ---- consumeActionError (Q4: failure-survives-popup-close replay) ----
// Mirrors the shape of main.qml's root item that CGroupDelegate/
// AddServicePage read: lastActionConsumed/pendingActionKey/lastActionResult.
{
    function makeRoot(overrides) {
        return Object.assign({
            lastActionConsumed: true,
            pendingActionKey: "",
            lastActionResult: null,
        }, overrides);
    }

    // Nothing pending yet -> null, doesn't touch lastActionConsumed.
    {
        const r = makeRoot({});
        eq(L.consumeActionError(r, "app-foo.service"), null,
           "consumeActionError: no result at all -> null");
        eq(r.lastActionConsumed, true, "consumeActionError: no-op leaves lastActionConsumed untouched");
    }

    // Unconsumed failure, matching key -> hint surfaces, marked consumed.
    {
        const r = makeRoot({
            lastActionConsumed: false,
            pendingActionKey: "app-foo.service",
            lastActionResult: { ok: false, hint: "invalid memory value" },
        });
        eq(L.consumeActionError(r, "app-foo.service"), "invalid memory value",
           "consumeActionError: unconsumed failure + matching key -> hint");
        eq(r.lastActionConsumed, true, "consumeActionError: marks the result consumed so it isn't replayed twice");
    }

    // Missing hint text falls back to the generic "action failed" string.
    {
        const r = makeRoot({
            lastActionConsumed: false,
            pendingActionKey: "app-foo.service",
            lastActionResult: { ok: false },
        });
        eq(L.consumeActionError(r, "app-foo.service"), "action failed",
           "consumeActionError: failure with no hint text -> generic fallback");
    }

    // Success must NEVER surface, even if unconsumed + key matches.
    {
        const r = makeRoot({
            lastActionConsumed: false,
            pendingActionKey: "app-foo.service",
            lastActionResult: { ok: true },
        });
        eq(L.consumeActionError(r, "app-foo.service"), null,
           "consumeActionError: successful result -> null, never surfaced as an error");
        eq(r.lastActionConsumed, false, "consumeActionError: a success does not mark anything consumed");
    }

    // Mismatched key (e.g. another row's edit, or the add-service page)
    // must NOT surface here, and must NOT be consumed -- the row/page it
    // actually belongs to still needs to see it.
    {
        const r = makeRoot({
            lastActionConsumed: false,
            pendingActionKey: "__add__",
            lastActionResult: { ok: false, hint: "boom" },
        });
        eq(L.consumeActionError(r, "app-foo.service"), null,
           "consumeActionError: mismatched key -> null, not this row's result");
        eq(r.lastActionConsumed, false,
           "consumeActionError: mismatched key leaves lastActionConsumed for the real owner to consume");
    }

    // Already-consumed failure, even with a matching key, must not replay.
    {
        const r = makeRoot({
            lastActionConsumed: true,
            pendingActionKey: "app-foo.service",
            lastActionResult: { ok: false, hint: "boom" },
        });
        eq(L.consumeActionError(r, "app-foo.service"), null,
           "consumeActionError: already-consumed failure -> null, not replayed a second time");
    }
}

console.log(failures === 0 ? "\nALL TESTS PASSED" : `\n${failures} FAILURES`);
process.exit(failures === 0 ? 0 : 1);
