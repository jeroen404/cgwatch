import QtQuick
import org.kde.plasma.plasma5support as Plasma5Support
import "logic.js" as Logic

// The only file that touches Plasma5Support (deprecation firewall). One poll
// = one "cgwatch-cli dump" run through the executable dataengine (dumpSource);
// apply/unlimit share a second, independent DataSource (actionSource) so an
// in-flight action never blocks or gets confused with the poll cycle. Both
// are correlated by the literal command string the dataengine was given
// (its own source-keying), each guarded by its own watchdog Timer.
//
// Note: buildDumpCommand/buildApplyCommand/buildUnlimitCommand in logic.js
// all bake a "CGWATCH_POLL=<nonce>" env-var prefix into the command string
// they return (dedup-buster for the exec dataengine only -- cgwatch-cli
// itself never reads it); this file does not add its own prefix on top.
//
// CRITICAL: the nonce is STABLE per widget instance (instanceToken), NOT
// per poll. The exec dataengine keys sources by the literal command and
// backs them with a QQmlOpenMetaObject that GROWS a property per distinct
// source key and never shrinks it. A per-poll nonce therefore leaked one
// meta-object property every 2s; after hours the O(N) toMetaObject()
// rebuild on each connect/disconnect pegged plasmashell's main thread at
// 100% CPU (confirmed via gdb + an A/B DataSource repro). A stable key
// keeps the source set at size 1, so connect/reconnect stays O(1).
Item {
    id: api

    property string helperCommand: "cgwatch-cli"
    property int requestTimeout: 10

    readonly property bool pollInFlight: currentPollCmd !== ""
    readonly property bool actionInFlight: currentActionCmd !== ""

    // result: the discriminated union from Logic.parseHelperOutput()
    // ({ok:true, kind, data} or {ok:false, kind, hint}).
    signal pollFinished(var result)
    signal actionFinished(var result)

    property string currentPollCmd: ""
    property string currentActionCmd: ""
    // "apply" | "unlimit" -- which command currentActionCmd is, so its
    // "exited" handler can pass the right expectedKind to
    // Logic.parseHelperOutput() (version-skew detection).
    property string currentActionKind: ""
    // Two widget instances must never generate identical source strings: the
    // exec dataengine keys sources by the literal command and would coalesce
    // their connect/disconnect lifecycles. This is per-instance and stable
    // for the widget's lifetime (see the meta-object-growth note above for
    // why it must NOT vary per poll).
    readonly property string instanceToken: Math.random().toString(36).slice(2, 10)

    function cfgLogic() {
        return { helperCommand: helperCommand }
    }

    // Returns false when a poll is already in flight (caller decides to skip
    // or abort()+poll() again).
    function poll() {
        if (currentPollCmd !== "")
            return false
        var nonce = instanceToken + "-p"
        currentPollCmd = Logic.buildDumpCommand(cfgLogic(), nonce)
        pollWatchdog.interval = (Math.max(1, requestTimeout) + 5) * 1000
        pollWatchdog.restart()
        dumpSource.connectSource(currentPollCmd)
        return true
    }

    function abort() {
        if (currentPollCmd === "")
            return
        dumpSource.disconnectSource(currentPollCmd)
        currentPollCmd = ""
        pollWatchdog.stop()
    }

    // mem/cpu: raw user-typed strings, or "" / null / undefined to leave that
    // key untouched (see logic.js buildApplyCommand). isEdit: mirrors the
    // TUI's EditLimitsModal save path (cgwatch-cli apply --edit) -- see
    // main.qml's requestEditApply/requestApply.
    function applyLimits(unit, mem, cpu, isEdit) {
        var nonce = instanceToken + "-a"
        return runAction(Logic.buildApplyCommand(unit, mem, cpu, cfgLogic(), nonce, isEdit), "apply")
    }

    function unlimit(unit) {
        var nonce = instanceToken + "-a"
        return runAction(Logic.buildUnlimitCommand(unit, cfgLogic(), nonce), "unlimit")
    }

    // Returns false when an action is already in flight (no queueing here --
    // the caller, e.g. the inline editor, disables Save/Unlimit while
    // actionInFlight is true). kind is only recorded once the action is
    // actually accepted, so a call that bounces off the in-flight guard
    // can never clobber the kind of the action that's still running.
    function runAction(cmd, kind) {
        if (currentActionCmd !== "")
            return false
        currentActionCmd = cmd
        currentActionKind = kind
        actionWatchdog.interval = (Math.max(1, requestTimeout) + 5) * 1000
        actionWatchdog.restart()
        actionSource.connectSource(currentActionCmd)
        return true
    }

    function abortAction() {
        if (currentActionCmd === "")
            return
        actionSource.disconnectSource(currentActionCmd)
        currentActionCmd = ""
        actionWatchdog.stop()
    }

    Plasma5Support.DataSource {
        id: dumpSource
        engine: "executable"
        connectedSources: []
        onNewData: function (sourceName, data) {
            disconnectSource(sourceName)
            if (sourceName !== api.currentPollCmd)
                return // straggler from an aborted poll
            api.currentPollCmd = ""
            pollWatchdog.stop()
            var exitCode = data["exit code"]
            var result = Logic.parseHelperOutput(data["stdout"] || "", data["stderr"] || "",
                                                  exitCode === undefined ? -1 : exitCode, "dump")
            api.pollFinished(result)
        }
    }

    Plasma5Support.DataSource {
        id: actionSource
        engine: "executable"
        connectedSources: []
        onNewData: function (sourceName, data) {
            disconnectSource(sourceName)
            if (sourceName !== api.currentActionCmd)
                return // straggler from an aborted/superseded action
            api.currentActionCmd = ""
            actionWatchdog.stop()
            var exitCode = data["exit code"]
            var result = Logic.parseHelperOutput(data["stdout"] || "", data["stderr"] || "",
                                                  exitCode === undefined ? -1 : exitCode,
                                                  api.currentActionKind)
            api.actionFinished(result)
        }
    }

    Timer {
        id: pollWatchdog
        repeat: false
        onTriggered: {
            if (api.currentPollCmd === "")
                return
            console.log("cgwatch: poll watchdog fired, aborting")
            dumpSource.disconnectSource(api.currentPollCmd)
            api.currentPollCmd = ""
            api.pollFinished({ ok: false, kind: "crash",
                               hint: "cgwatch-cli did not respond within " + api.requestTimeout + "s" })
        }
    }

    Timer {
        id: actionWatchdog
        repeat: false
        onTriggered: {
            if (api.currentActionCmd === "")
                return
            console.log("cgwatch: action watchdog fired, aborting")
            actionSource.disconnectSource(api.currentActionCmd)
            api.currentActionCmd = ""
            api.actionFinished({ ok: false, kind: "crash",
                                 hint: "cgwatch-cli did not respond within " + api.requestTimeout + "s" })
        }
    }
}
